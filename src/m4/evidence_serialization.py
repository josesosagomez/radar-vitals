"""Portable, lossless serializers for M8 M3 estimator evidence.

The radar artifact uses two deliberately small formats:

* table evidence is represented only by ordinary non-object NumPy arrays; ragged
  scientific arrays are padded and accompanied by explicit presence masks;
* the production estimator's native nested payload is described by a deterministic
  JSON tree whose array leaves live unchanged in an NPZ file.

Neither format accepts a raw frame cube.  The artifact records hashes of the common
cube/window instead; persisting the ADC cube here would duplicate a raw input and make
the evidence bundle unnecessarily sensitive.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from typing import Mapping, Sequence

import numpy as np

__all__ = [
    "EVIDENCE_COMPUTED",
    "EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE",
    "EVIDENCE_STATUSES",
    "NativeTree",
    "array_sha256",
    "deserialize_native_tree",
    "pack_ahmed_evidence",
    "pack_shared_evidence",
    "serialize_native_tree",
    "validate_npz_arrays",
]

#: Whether one stage's persisted evidence arrays were actually produced by running that
#: stage, or are an explicitly labelled placeholder.  The label is required because the
#: arrays themselves are ambiguous: under this codebase's keep-mask convention an
#: all-False ``suppression_mask`` reads as "every candidate was suppressed by the arm's
#: rule", and an all-NaN pre-suppression score array reads as "nothing scored", when in
#: fact no rule was evaluated and no accumulation was run.  CLAUDE.md section 4 forbids
#: presenting such placeholder values as if they were computed results.
EVIDENCE_COMPUTED = "computed"
#: The Ahmed heart stage needs the same-H breathing bin to define its suppression rule.
#: When the breathing estimate is invalid there is no such bin, so the heart stage is
#: never run and its row is a placeholder that still aligns with the candidate bins.
EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE = (
    "not_computed_breathing_dependency_unavailable"
)
EVIDENCE_STATUSES = (EVIDENCE_COMPUTED, EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE)


def array_sha256(values: np.ndarray) -> str:
    """Hash dtype, shape, and C-order bytes, including zero-length arrays."""
    array = np.asarray(values)
    if array.dtype == object:
        raise TypeError("object arrays cannot be hashed as portable evidence")
    header = f"dtype={array.dtype.str}|shape={array.shape}".encode("utf-8")
    digest = sha256(header + b"\x00")
    digest.update(np.ascontiguousarray(array).tobytes(order="C"))
    return digest.hexdigest()


def validate_npz_arrays(arrays: Mapping[str, np.ndarray]) -> None:
    """Validate an NPZ payload before writing it with ``allow_pickle=False``."""
    for key, value in arrays.items():
        if not isinstance(key, str) or not key:
            raise TypeError("NPZ keys must be non-empty strings")
        array = np.asarray(value)
        if array.dtype == object:
            raise TypeError(f"{key}: object dtype is forbidden")


@dataclass(frozen=True)
class NativeTree:
    """JSON index plus its non-object NPZ array leaves."""

    index: dict
    arrays: Mapping[str, np.ndarray]


def serialize_native_tree(value: object) -> NativeTree:
    """Serialize a production native payload without losing Python/NumPy types.

    Dict insertion order, list/tuple identity, exact Python scalar types, ndarray
    dtype/shape, and every ``np.generic`` dtype are retained.  Repeated leaves are
    copied independently; recursive cycles and unsupported objects are fatal.
    """
    arrays: dict[str, np.ndarray] = {}
    active_container_ids: set[int] = set()

    def add_array(array: np.ndarray, *, numpy_scalar: bool) -> dict:
        values = np.asarray(array)
        if values.dtype == object:
            raise TypeError("native payload contains an object-dtype NumPy value")
        key = f"array_{len(arrays):08d}"
        arrays[key] = np.array(values, copy=True, order="C")
        return {
            "type": "numpy_scalar" if numpy_scalar else "ndarray",
            "array_key": key,
        }

    def visit(item: object, path: tuple[str, ...]) -> dict:
        if isinstance(item, np.ndarray):
            # A decoded radar cube is structurally a complex array with frame/chirp/RX/
            # fast-time axes.  Reject that structure regardless of what a caller names
            # the containing key; production native evidence contains only scalar,
            # vector, and matrix diagnostics.
            if item.ndim >= 4 and np.issubdtype(item.dtype, np.complexfloating):
                raise ValueError(f"raw frame cube is forbidden at {'.'.join(path)}")
            return add_array(item, numpy_scalar=False)
        if isinstance(item, np.generic):
            return add_array(np.asarray(item), numpy_scalar=True)
        if item is None:
            return {"type": "none"}
        if type(item) is bool:
            return {"type": "bool", "value": item}
        if type(item) is int:
            return {"type": "int", "value": item}
        if type(item) is float:
            if math.isnan(item):
                return {"type": "float_nonfinite", "value": "nan"}
            if math.isinf(item):
                return {
                    "type": "float_nonfinite",
                    "value": "positive_infinity" if item > 0 else "negative_infinity",
                }
            return {"type": "float", "value": item}
        if type(item) is str:
            return {"type": "str", "value": item}
        if type(item) is dict:
            identity = id(item)
            if identity in active_container_ids:
                raise ValueError(f"cycle detected at {'.'.join(path) or '<root>'}")
            active_container_ids.add(identity)
            try:
                entries = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise TypeError(
                            f"native mapping key at {'.'.join(path) or '<root>'} "
                            f"must be exact str, got {type(key).__name__}"
                        )
                    entries.append({"key": key, "value": visit(child, (*path, key))})
                return {"type": "dict", "entries": entries}
            finally:
                active_container_ids.remove(identity)
        if type(item) in (list, tuple):
            identity = id(item)
            if identity in active_container_ids:
                raise ValueError(f"cycle detected at {'.'.join(path) or '<root>'}")
            active_container_ids.add(identity)
            try:
                children = [visit(child, (*path, str(i))) for i, child in enumerate(item)]
                return {"type": "tuple" if type(item) is tuple else "list", "items": children}
            finally:
                active_container_ids.remove(identity)
        raise TypeError(
            f"unsupported native value {type(item).__name__} at "
            f"{'.'.join(path) or '<root>'}"
        )

    index = {"schema_version": 1, "root": visit(value, ())}
    validate_npz_arrays(arrays)
    return NativeTree(index=index, arrays=arrays)


def deserialize_native_tree(index: Mapping[str, object], arrays: Mapping[str, np.ndarray]) -> object:
    """Reconstruct a tree produced by :func:`serialize_native_tree`."""
    if not isinstance(index, Mapping) or set(index) != {"schema_version", "root"}:
        raise ValueError("malformed or unsupported native-tree index schema")
    if type(index["schema_version"]) is not int or index["schema_version"] != 1:
        raise ValueError("unsupported or malformed native-tree index")
    if not isinstance(arrays, Mapping):
        raise TypeError("native-tree arrays must be a mapping")

    referenced: set[str] = set()

    def exact_keys(node: Mapping[str, object], expected: set[str], kind: str) -> None:
        if set(node) != expected:
            raise ValueError(f"malformed {kind} native-tree node")

    def restore(node: object) -> object:
        if not isinstance(node, Mapping) or type(node.get("type")) is not str:
            raise ValueError("malformed native-tree node")
        kind = node["type"]
        if kind == "none":
            exact_keys(node, {"type"}, kind)
            return None
        if kind in ("bool", "int", "float", "str"):
            exact_keys(node, {"type", "value"}, kind)
            expected = {"bool": bool, "int": int, "float": float, "str": str}[kind]
            value = node.get("value")
            if type(value) is not expected:
                raise ValueError(f"malformed {kind} node")
            return value
        if kind == "float_nonfinite":
            exact_keys(node, {"type", "value"}, kind)
            token = node["value"]
            if type(token) is not str or token not in {
                "nan", "positive_infinity", "negative_infinity"
            }:
                raise ValueError("malformed float_nonfinite native-tree node")
            return {
                "nan": float("nan"),
                "positive_infinity": float("inf"),
                "negative_infinity": float("-inf"),
            }[token]
        if kind in ("ndarray", "numpy_scalar"):
            exact_keys(node, {"type", "array_key"}, kind)
            key = node["array_key"]
            if type(key) is not str or key not in arrays:
                raise ValueError(f"missing NPZ array leaf {key!r}")
            if key in referenced:
                raise ValueError(f"malformed duplicate NPZ array reference {key!r}")
            referenced.add(key)
            source_array = arrays[key]
            if not isinstance(source_array, np.ndarray):
                raise TypeError(f"{key}: NPZ leaf must be an ndarray")
            array = np.array(source_array, copy=True)
            if array.dtype == object:
                raise TypeError(f"{key}: object dtype is forbidden")
            if kind == "numpy_scalar":
                if array.shape != ():
                    raise ValueError(f"{key}: NumPy scalar leaf must be zero-dimensional")
                return array[()]
            return array
        if kind == "dict":
            exact_keys(node, {"type", "entries"}, kind)
            entries = node["entries"]
            if type(entries) is not list:
                raise ValueError("malformed dict entries in native-tree index")
            result: dict[str, object] = {}
            for entry in entries:
                if not isinstance(entry, Mapping) or set(entry) != {"key", "value"}:
                    raise ValueError("malformed dict entry in native-tree index")
                key = entry["key"]
                if type(key) is not str or key in result:
                    raise ValueError("malformed or duplicate dict key in native-tree index")
                result[key] = restore(entry["value"])
            return result
        if kind in ("list", "tuple"):
            exact_keys(node, {"type", "items"}, kind)
            items = node["items"]
            if type(items) is not list:
                raise ValueError(f"malformed {kind} items in native-tree index")
            values = [restore(child) for child in items]
            return tuple(values) if kind == "tuple" else values
        raise ValueError(f"unknown native-tree node type {kind!r}")

    result = restore(index["root"])
    if referenced != set(arrays):
        raise ValueError("native-tree NPZ contains missing or unreferenced array leaves")
    return result


def _fixed_unicode(values: Sequence[object]) -> np.ndarray:
    text = [str(value) for value in values]
    width = max((len(value) for value in text), default=1)
    return np.asarray(text, dtype=f"<U{width}")


def _stack_equal_shape(records: Sequence[Mapping[str, object]], key: str) -> np.ndarray:
    arrays = []
    for record in records:
        value = record[key]
        if not isinstance(value, np.ndarray):
            raise TypeError(f"shared evidence field {key!r} must be an ndarray")
        if value.dtype == object:
            raise TypeError(f"shared evidence field {key!r} has object dtype")
        arrays.append(value)
    if not arrays:
        return np.empty((0, 0), dtype=np.float64)
    shape = arrays[0].shape
    dtype = arrays[0].dtype
    if any(array.shape != shape or array.dtype != dtype for array in arrays):
        raise ValueError(f"shared evidence field {key!r} changed shape or dtype")
    return np.stack(arrays)


def pack_shared_evidence(records: Sequence[Mapping[str, object]]) -> dict[str, np.ndarray]:
    """Pack one row per ``(capture, lock, k)`` without object arrays."""
    for record in records:
        for key in ("sample_time_s", "phase", "frequency_grid_hz", "spectrum_magnitude"):
            value = record[key]
            if not isinstance(value, np.ndarray) or value.ndim != 1 or value.dtype != np.float64:
                raise TypeError(f"shared evidence {key} must be a one-dimensional float64 ndarray")
        for key in ("locked_bin", "k", "frame_start", "frame_stop"):
            if type(record[key]) is not int:
                raise TypeError(f"shared evidence {key} must be an exact integer")
        for key in ("epoch_start", "epoch_stop"):
            if type(record[key]) is not float or not np.isfinite(record[key]):
                raise TypeError(f"shared evidence {key} must be a finite float")
    arrays = {
        "capture_id": _fixed_unicode([r["capture_id"] for r in records]),
        "lock_estimand_id": _fixed_unicode([r["lock_estimand_id"] for r in records]),
        "locked_bin": np.asarray([r["locked_bin"] for r in records], dtype=np.int64),
        "k": np.asarray([r["k"] for r in records], dtype=np.int64),
        "frame_start": np.asarray([r["frame_start"] for r in records], dtype=np.int64),
        "frame_stop": np.asarray([r["frame_stop"] for r in records], dtype=np.int64),
        "epoch_start": np.asarray([r["epoch_start"] for r in records], dtype=np.float64),
        "epoch_stop": np.asarray([r["epoch_stop"] for r in records], dtype=np.float64),
        "sample_time_s": _stack_equal_shape(records, "sample_time_s"),
        "phase": _stack_equal_shape(records, "phase"),
        "frequency_grid_hz": _stack_equal_shape(records, "frequency_grid_hz"),
        "spectrum_magnitude": _stack_equal_shape(records, "spectrum_magnitude"),
        "cube_hash": _fixed_unicode([r["cube_hash"] for r in records]),
        "window_cube_hash": _fixed_unicode([r["window_cube_hash"] for r in records]),
        "signal_hash": _fixed_unicode([r["signal_hash"] for r in records]),
        "config_hash": _fixed_unicode([r["config_hash"] for r in records]),
        "source_hash": _fixed_unicode([r["source_hash"] for r in records]),
        "run_hash": _fixed_unicode([r["run_hash"] for r in records]),
    }
    validate_npz_arrays(arrays)
    return arrays


def pack_ahmed_evidence(records: Sequence[Mapping[str, object]]) -> dict[str, np.ndarray]:
    """Pack both Ahmed breathing and heart evidence with explicit ragged masks."""
    n_rows = len(records)
    arrays = {
        "capture_id": _fixed_unicode([r["capture_id"] for r in records]),
        "lock_estimand_id": _fixed_unicode([r["lock_estimand_id"] for r in records]),
        "arm_id": _fixed_unicode([r["arm_id"] for r in records]),
        "k": np.asarray([r["k"] for r in records], dtype=np.int64),
        "cube_hash": _fixed_unicode([r["cube_hash"] for r in records]),
        "signal_hash": _fixed_unicode([r["signal_hash"] for r in records]),
        "config_hash": _fixed_unicode([r["config_hash"] for r in records]),
        "source_hash": _fixed_unicode([r["source_hash"] for r in records]),
        "run_hash": _fixed_unicode([r["run_hash"] for r in records]),
    }

    def optional_numeric(name: str, dtype) -> tuple[np.ndarray, np.ndarray]:
        present = np.asarray([r[name] is not None for r in records], dtype=bool)
        values = np.zeros(n_rows, dtype=dtype)
        expected_type = int if np.issubdtype(np.dtype(dtype), np.integer) else float
        for i, record in enumerate(records):
            if present[i]:
                if type(record[name]) is not expected_type:
                    raise TypeError(f"{name} has malformed {expected_type.__name__} value")
                if expected_type is float and not np.isfinite(record[name]):
                    raise ValueError(f"{name} must be finite when present")
                values[i] = record[name]
        return values, present

    def validate_stage_record(record: Mapping[str, object], stage: str) -> None:
        candidates = record[f"{stage}_candidate_bins"]
        matrix = record[f"{stage}_harmonic_bins"]
        if not isinstance(candidates, np.ndarray) or not np.issubdtype(
            candidates.dtype, np.integer
        ) or candidates.dtype == np.bool_ or candidates.ndim != 1:
            raise TypeError(f"{stage} candidate bins must be a one-dimensional integer ndarray")
        if candidates.size and (
            np.any(candidates < 0) or np.any(np.diff(candidates.astype(np.int64)) <= 0)
        ):
            raise ValueError(f"{stage} candidate bins are malformed or non-increasing")
        if not isinstance(matrix, np.ndarray) or not np.issubdtype(
            matrix.dtype, np.integer
        ) or matrix.dtype == np.bool_ or matrix.ndim != 2:
            raise TypeError(f"{stage} harmonic bins must be a two-dimensional integer ndarray")
        if matrix.shape[0] != candidates.size:
            raise ValueError(f"{stage} harmonic-bin rows must align with candidate bins")
        expected_matrix = candidates.astype(np.int64)[:, None] * np.arange(
            1, matrix.shape[1] + 1, dtype=np.int64
        )[None, :]
        if not np.array_equal(matrix, expected_matrix):
            raise ValueError(f"{stage} harmonic-bin values are malformed")
        for name in ("support_mask", "nyquist_mask", "suppression_mask", "eligibility_mask"):
            mask = record[f"{stage}_{name}"]
            if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_:
                raise TypeError(f"{stage}_{name} must have bool dtype")
            if mask.shape != candidates.shape:
                raise ValueError(f"{stage}_{name} must align with candidate bins")
        for name in ("score_pre_suppression", "score_post_suppression"):
            scores = record[f"{stage}_{name}"]
            if not isinstance(scores, np.ndarray) or scores.dtype != np.float64:
                raise TypeError(f"{stage}_{name} must have float64 dtype")
            if scores.shape != candidates.shape or np.any(np.isinf(scores)):
                raise ValueError(f"{stage}_{name} is malformed")
        for name in ("selected_bin", "runner_up_bin"):
            value = record[f"{stage}_{name}"]
            if value is not None and (type(value) is not int or value not in candidates):
                raise ValueError(f"{stage}_{name} is not a declared candidate")
        for name in (
            "selected_frequency_hz",
            "selected_score",
            "runner_up_frequency_hz",
            "runner_up_score",
            "reported_bpm",
        ):
            value = record[f"{stage}_{name}"]
            if value is not None and (type(value) is not float or not np.isfinite(value)):
                raise TypeError(f"{stage}_{name} must be a finite float or None")
        selected_bin = record[f"{stage}_selected_bin"]
        selected_score = record[f"{stage}_selected_score"]
        if (selected_bin is None) != (selected_score is None):
            raise ValueError(f"{stage} selected bin/score presence is inconsistent")
        if selected_bin is not None:
            selected_index = int(np.flatnonzero(candidates == selected_bin)[0])
            scores = record[f"{stage}_score_post_suppression"]
            if not np.isfinite(scores[selected_index]) or scores[selected_index] != selected_score:
                raise ValueError(f"{stage} selected score does not match its candidate")
        if record[f"{stage}_valid"]:
            frequency = record[f"{stage}_selected_frequency_hz"]
            reported_bpm = record[f"{stage}_reported_bpm"]
            if frequency is None or reported_bpm is None or not np.isclose(
                reported_bpm, 60.0 * frequency, rtol=0.0, atol=1e-12
            ):
                raise ValueError(f"{stage} valid reported rate is not reconstructable")

    for stage in ("breath", "heart"):
        for record in records:
            if type(record[f"{stage}_valid"]) is not bool:
                raise TypeError(f"{stage}_valid must be an exact bool")
            if type(record[f"{stage}_reason"]) is not str:
                raise TypeError(f"{stage}_reason must be an exact str")
            if record[f"{stage}_evidence_status"] not in EVIDENCE_STATUSES:
                raise ValueError(
                    f"{stage}_evidence_status must be one of {EVIDENCE_STATUSES}"
                )
            validate_stage_record(record, stage)
        candidate_key = f"{stage}_candidate_bins"
        harmonic_key = f"{stage}_harmonic_bins"
        max_candidates = max(
            (len(np.asarray(record[candidate_key])) for record in records), default=0
        )
        max_harmonics = max(
            (
                np.asarray(record[harmonic_key]).shape[1]
                if np.asarray(record[harmonic_key]).ndim == 2
                else 0
                for record in records
            ),
            default=0,
        )
        candidate_bins = np.zeros((n_rows, max_candidates), dtype=np.int64)
        candidate_present = np.zeros((n_rows, max_candidates), dtype=bool)
        harmonic_bins = np.zeros(
            (n_rows, max_candidates, max_harmonics), dtype=np.int64
        )
        harmonic_present = np.zeros_like(harmonic_bins, dtype=bool)
        masks = {
            name: np.zeros((n_rows, max_candidates), dtype=bool)
            for name in (
                "support_mask",
                "nyquist_mask",
                "suppression_mask",
                "eligibility_mask",
            )
        }
        score_pre = np.zeros((n_rows, max_candidates), dtype=np.float64)
        score_post = np.zeros((n_rows, max_candidates), dtype=np.float64)
        score_pre_present = np.zeros((n_rows, max_candidates), dtype=bool)
        score_post_present = np.zeros((n_rows, max_candidates), dtype=bool)

        for row_index, record in enumerate(records):
            candidates = record[candidate_key]
            if not isinstance(candidates, np.ndarray) or not np.issubdtype(
                candidates.dtype, np.integer
            ) or np.issubdtype(candidates.dtype, np.bool_):
                raise TypeError(f"{stage} candidate bins must have integer dtype")
            if candidates.ndim != 1 or np.any(candidates < 0):
                raise ValueError(f"{stage} candidate bins are malformed")
            if candidates.size and np.max(candidates) > np.iinfo(np.int64).max:
                raise ValueError(f"{stage} candidate bins exceed int64 range")
            candidates = candidates.astype(np.int64, copy=False)
            count = candidates.size
            candidate_bins[row_index, :count] = candidates
            candidate_present[row_index, :count] = True
            matrix = record[harmonic_key]
            if not isinstance(matrix, np.ndarray) or not np.issubdtype(
                matrix.dtype, np.integer
            ) or np.issubdtype(matrix.dtype, np.bool_):
                raise TypeError(f"{stage} harmonic bins must have integer dtype")
            if matrix.ndim != 2 or matrix.shape[0] != count:
                raise ValueError(
                    f"{stage} harmonic-bin rows must align with candidate bins"
                )
            if np.any(matrix < 0) or (
                matrix.size and np.max(matrix) > np.iinfo(np.int64).max
            ):
                raise ValueError(f"{stage} harmonic bins are malformed")
            matrix = matrix.astype(np.int64, copy=False)
            harmonic_bins[row_index, :count, : matrix.shape[1]] = matrix
            support = record[f"{stage}_support_mask"]
            if not isinstance(support, np.ndarray) or support.dtype != np.bool_:
                raise TypeError(f"{stage} support mask must have bool dtype")
            if support.shape != (count,):
                raise ValueError(f"{stage} support mask must align with candidate bins")
            # Presence marks actual matrix cells, including scientifically unsupported
            # candidates. Only ragged padding is absent; support has its own mask.
            harmonic_present[row_index, :count, : matrix.shape[1]] = True
            for name, target in masks.items():
                values = record[f"{stage}_{name}"]
                if not isinstance(values, np.ndarray) or values.dtype != np.bool_:
                    raise TypeError(f"{stage}_{name} must have bool dtype")
                if values.shape != (count,):
                    raise ValueError(f"{stage}_{name} must align with candidate bins")
                target[row_index, :count] = values
            for name, target, present in (
                ("score_pre_suppression", score_pre, score_pre_present),
                ("score_post_suppression", score_post, score_post_present),
            ):
                values = record[f"{stage}_{name}"]
                if not isinstance(values, np.ndarray) or values.dtype != np.float64:
                    raise TypeError(f"{stage}_{name} must have float64 dtype")
                if values.shape != (count,):
                    raise ValueError(f"{stage}_{name} must align with candidate bins")
                finite = np.isfinite(values)
                target[row_index, :count][finite] = values[finite]
                present[row_index, :count] = finite

        arrays.update(
            {
                f"{stage}_candidate_bins": candidate_bins,
                f"{stage}_candidate_present": candidate_present,
                f"{stage}_harmonic_bins": harmonic_bins,
                f"{stage}_harmonic_present": harmonic_present,
                f"{stage}_score_pre_suppression": score_pre,
                f"{stage}_score_pre_suppression_present": score_pre_present,
                f"{stage}_score_post_suppression": score_post,
                f"{stage}_score_post_suppression_present": score_post_present,
                f"{stage}_valid": np.asarray(
                    [record[f"{stage}_valid"] for record in records], dtype=bool
                ),
                f"{stage}_reason": _fixed_unicode(
                    [record[f"{stage}_reason"] for record in records]
                ),
                f"{stage}_evidence_status": _fixed_unicode(
                    [record[f"{stage}_evidence_status"] for record in records]
                ),
                **{f"{stage}_{name}": value for name, value in masks.items()},
            }
        )
        for name, dtype in (
            ("selected_bin", np.int64),
            ("selected_frequency_hz", np.float64),
            ("selected_score", np.float64),
            ("runner_up_bin", np.int64),
            ("runner_up_frequency_hz", np.float64),
            ("runner_up_score", np.float64),
            ("reported_bpm", np.float64),
        ):
            values, present = optional_numeric(f"{stage}_{name}", dtype)
            arrays[f"{stage}_{name}"] = values
            arrays[f"{stage}_{name}_present"] = present

    validate_npz_arrays(arrays)
    return arrays
