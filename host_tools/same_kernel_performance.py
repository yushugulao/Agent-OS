#!/usr/bin/env python3
"""Normalize same-kernel compat/native measurements without overstating claims."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from statistics import median
from typing import Any, Iterable

try:
    from . import contest_demo
    from .strict_json import strict_json_loads
except ImportError:
    import contest_demo
    from strict_json import strict_json_loads


SCHEMA_VERSION = 1
KIND = "agentos-same-kernel-performance"
REPORT_KIND = "agentos-contest-showcase"
PROTOCOL_KIND = "agentos-same-kernel-protocol"
MIN_CONTROLLED_RUNS = 8
LANES = ("compat", "native")
MECHANISM_MODES = ("compat", "native", "workflow")
CAMPAIGN_MECHANISM_MODES = (*MECHANISM_MODES, "runtime_probe")
COUNTER_SCOPES = {
    "default": "global",
    "workload_syscalls": "observer_process",
}
MECHANISM_FIELDS = (
    "epoch_commits",
    "epoch_buffers_staged",
    "physical_writes",
    "physical_reads",
    "durable_flushes",
    "deduplicated_stages",
    "cow_shared_pages",
    "cow_copied_pages",
    "cow_fault_promotions",
    "exec_cache_hits",
    "exec_cache_misses",
    "exec_cache_shared_pages",
    "exec_cache_evictions",
    "workload_syscalls",
    "directory_block_probes",
    "directory_entries_examined",
    "virtio_notifications",
    "virtio_submitted_requests",
    "virtio_write_batch_calls",
    "virtio_batched_write_requests",
    "virtio_indirect_write_batch_calls",
    "virtio_read_batch_calls",
    "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
    "metadata_requests",
    "metadata_coalesced",
    "metadata_commits",
)
PAIR_FIELDS = (
    ("physical_writes", "count"),
    ("physical_reads", "count"),
    ("durable_flushes", "count"),
    ("epoch_commits", "count"),
    ("epoch_buffers_staged", "count"),
    ("buffers_per_epoch", "buffers/epoch"),
    ("deduplicated_stages", "count"),
    ("workload_syscalls", "count"),
    ("directory_block_probes", "block"),
    ("directory_entries_examined", "entry"),
    ("virtio_notifications", "notify"),
    ("virtio_submitted_requests", "request"),
    ("virtio_write_batch_calls", "batch"),
    ("virtio_batched_write_requests", "request"),
    ("virtio_indirect_write_batch_calls", "batch"),
    ("virtio_read_batch_calls", "batch"),
    ("virtio_batched_read_requests", "request"),
    ("overwrite_prereads_skipped", "block"),
    ("metadata_requests", "request"),
    ("metadata_coalesced", "request"),
    ("metadata_commits", "commit"),
    ("directory_entries_per_probe", "entry/block"),
    ("virtio_requests_per_notification", "request/notify"),
    ("virtio_write_requests_per_batch", "request/batch"),
    ("virtio_read_requests_per_batch", "request/batch"),
    ("virtio_batched_request_share_pct", "%"),
    ("metadata_coalescing_rate_pct", "%"),
    ("metadata_requests_per_commit", "request/commit"),
)
WORKFLOW_FIELDS = (
    ("cow_shared_pages", "pages"),
    ("cow_copied_pages", "pages"),
    ("cow_fault_promotions", "pages"),
    ("exec_cache_hits", "count"),
    ("exec_cache_misses", "count"),
    ("exec_cache_shared_pages", "pages"),
    ("exec_cache_evictions", "count"),
)
RAW_MECHANISM_FIELDS = MECHANISM_FIELDS + ("metadata_dirty", "metadata_durable")
FENCE_COUNTER_FIELDS = tuple(
    field
    for field in RAW_MECHANISM_FIELDS
    if not field.startswith("cow_") and not field.startswith("exec_cache_")
) + ("metadata_pending",)
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class SameKernelPerformanceError(RuntimeError):
    """Raised when a showcase summary cannot support numeric comparison."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SameKernelPerformanceError(f"{label} must be an object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SameKernelPerformanceError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SameKernelPerformanceError(f"{label} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise SameKernelPerformanceError(f"{label} must be {qualifier}")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise SameKernelPerformanceError(f"{label} must be a boolean")
    return value


def _ratio(
    left: int | float | None, right: int | float | None
) -> float | None:
    return round(left / right, 6) if left is not None and right else None


def _lane(report: dict[str, Any], name: str) -> dict[str, int]:
    comparison = _mapping(report.get("comparison"), "comparison")
    lanes = _mapping(comparison.get("lanes"), "comparison.lanes")
    lane = _mapping(lanes.get(name), f"comparison.lanes.{name}")
    result = {
        "duration_us": _integer(
            lane.get("duration_us"), f"{name} duration_us", positive=True
        ),
        "workload_syscalls": _integer(
            lane.get("workload_syscalls"), f"{name} workload_syscalls"
        ),
        "records_examined": _integer(
            lane.get("records_examined"), f"{name} records_examined"
        ),
        "bytes_read": _integer(lane.get("bytes_read"), f"{name} bytes_read"),
        "result_items": _integer(
            lane.get("result_items"), f"{name} result_items"
        ),
        "outcome_hash": _integer(
            lane.get("outcome_hash"), f"{name} outcome_hash"
        ),
    }
    if "end_to_end_duration_us" in lane:
        result["end_to_end_duration_us"] = _integer(
            lane["end_to_end_duration_us"],
            f"{name} end_to_end_duration_us",
            positive=True,
        )
    return result


def _mechanisms(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = _mapping(report.get("mechanisms"), "mechanisms")
    if set(raw) != set(MECHANISM_MODES):
        raise SameKernelPerformanceError(
            "mechanisms must contain compat, native, and workflow"
        )
    result: dict[str, dict[str, Any]] = {}
    for mode in MECHANISM_MODES:
        item = _mapping(raw[mode], f"mechanisms.{mode}")
        scope = _string(item.get("counter_scope"), f"{mode} counter_scope")
        normalized: dict[str, Any] = {"counter_scope": scope}
        for field in MECHANISM_FIELDS:
            normalized[field] = _integer(item.get(field), f"{mode} {field}")
        normalized["buffers_per_epoch"] = _ratio(
            normalized["epoch_buffers_staged"], normalized["epoch_commits"]
        )
        normalized["directory_entries_per_probe"] = _ratio(
            normalized["directory_entries_examined"],
            normalized["directory_block_probes"],
        )
        normalized["virtio_requests_per_notification"] = _ratio(
            normalized["virtio_submitted_requests"],
            normalized["virtio_notifications"],
        )
        normalized["virtio_write_requests_per_batch"] = _ratio(
            normalized["virtio_batched_write_requests"],
            normalized["virtio_write_batch_calls"],
        )
        normalized["virtio_read_requests_per_batch"] = _ratio(
            normalized["virtio_batched_read_requests"],
            normalized["virtio_read_batch_calls"],
        )
        batched_requests = (
            normalized["virtio_batched_write_requests"]
            + normalized["virtio_batched_read_requests"]
        )
        normalized["virtio_batched_request_share_pct"] = (
            None
            if normalized["virtio_submitted_requests"] == 0
            else round(
                100.0
                * batched_requests
                / normalized["virtio_submitted_requests"],
                6,
            )
        )
        normalized["metadata_coalescing_rate_pct"] = (
            None
            if normalized["metadata_requests"] == 0
            else round(
                100.0
                * normalized["metadata_coalesced"]
                / normalized["metadata_requests"],
                6,
            )
        )
        normalized["metadata_requests_per_commit"] = _ratio(
            normalized["metadata_requests"], normalized["metadata_commits"]
        )
        supplied_ratio = item.get("buffers_per_epoch")
        if supplied_ratio is not None and normalized["buffers_per_epoch"] is not None:
            if isinstance(supplied_ratio, bool) or not isinstance(
                supplied_ratio, (int, float)
            ):
                raise SameKernelPerformanceError(
                    f"{mode} buffers_per_epoch must be numeric"
                )
            if not any(
                math.isclose(
                    float(supplied_ratio), expected, rel_tol=1e-9, abs_tol=1e-9
                )
                for expected in (
                    normalized["buffers_per_epoch"],
                    round(normalized["buffers_per_epoch"], 3),
                )
            ):
                raise SameKernelPerformanceError(
                    f"{mode} buffers_per_epoch does not match raw counters"
                )
        if (
            normalized["virtio_notifications"]
            > normalized["virtio_submitted_requests"]
            or normalized["virtio_write_batch_calls"]
            + normalized["virtio_read_batch_calls"]
            > normalized["virtio_notifications"]
            or normalized["virtio_batched_write_requests"]
            < normalized["virtio_write_batch_calls"]
            or normalized["virtio_batched_read_requests"]
            < normalized["virtio_read_batch_calls"]
            or batched_requests > normalized["virtio_submitted_requests"]
            or normalized["virtio_indirect_write_batch_calls"]
            > normalized["virtio_write_batch_calls"]
            or normalized["metadata_coalesced"] > normalized["metadata_requests"]
        ):
            raise SameKernelPerformanceError(f"{mode} mechanism counters conflict")
        result[mode] = normalized
    if len({item["counter_scope"] for item in result.values()}) != 1:
        raise SameKernelPerformanceError("mechanism counter scopes are inconsistent")
    return result


def _protocol(value: object | None) -> dict[str, Any]:
    if value is None:
        return {
            "declared": False,
            "cache_state": "uncontrolled",
            "lane_order": ["compat", "native"],
            "setup_included": False,
            "quiescence_fence": False,
            "counter_isolation": "global_unisolated",
            "cow_exec_exercised_in_lanes": False,
        }
    raw = _mapping(value, "measurement protocol")
    if raw.get("schema_version") != 1 or raw.get("kind") != PROTOCOL_KIND:
        raise SameKernelPerformanceError("unsupported measurement protocol")
    cache_state = _string(raw.get("cache_state"), "protocol cache_state")
    if cache_state not in {"cold", "warm", "order_counterbalanced"}:
        raise SameKernelPerformanceError(
            "protocol cache_state must be cold, warm, or order_counterbalanced"
        )
    lane_order = raw.get("lane_order")
    if not isinstance(lane_order, list) or tuple(lane_order) not in {
        ("compat", "native"),
        ("native", "compat"),
    }:
        raise SameKernelPerformanceError(
            "protocol lane_order must contain compat/native exactly once"
        )
    counter_isolation = _string(
        raw.get("counter_isolation"), "protocol counter_isolation"
    )
    if counter_isolation not in {
        "scope_bound",
        "global_quiesced",
        "global_unisolated",
    }:
        raise SameKernelPerformanceError("unsupported protocol counter_isolation")
    return {
        "declared": True,
        "cache_state": cache_state,
        "lane_order": list(lane_order),
        "setup_included": _boolean(
            raw.get("setup_included"), "protocol setup_included"
        ),
        "quiescence_fence": _boolean(
            raw.get("quiescence_fence"), "protocol quiescence_fence"
        ),
        "counter_isolation": counter_isolation,
        "cow_exec_exercised_in_lanes": _boolean(
            raw.get("cow_exec_exercised_in_lanes"),
            "protocol cow_exec_exercised_in_lanes",
        ),
    }


def _validate_protocol_binding(
    protocol: dict[str, Any], summary_path: Path, summary_sha256: str, run_id: str
) -> None:
    if protocol.get("run_id") != run_id:
        raise SameKernelPerformanceError(
            f"protocol run_id does not bind {summary_path.name}"
        )
    if protocol.get("summary_sha256") != summary_sha256:
        raise SameKernelPerformanceError(
            f"protocol summary_sha256 does not bind {summary_path.name}"
        )


def normalize_report(
    report: dict[str, Any], *, protocol: dict[str, Any] | None = None
) -> dict[str, Any]:
    if report.get("kind") != REPORT_KIND or report.get("schema_version") != 4:
        raise SameKernelPerformanceError("unsupported contest showcase summary")
    run = _mapping(report.get("run"), "run")
    run_id = _string(run.get("id"), "run.id")
    commit = _string(run.get("commit"), "run.commit")
    comparison = _mapping(report.get("comparison"), "comparison")
    if comparison.get("design") != "same_kernel_same_guest_same_corpus":
        raise SameKernelPerformanceError("comparison is not a same-kernel design")
    timed_scope = _string(comparison.get("timed_scope"), "comparison.timed_scope")
    corpus_records = _integer(
        comparison.get("corpus_records"), "comparison.corpus_records", positive=True
    )
    actor_pid = _integer(
        comparison.get("execution_actor_pid"),
        "comparison.execution_actor_pid",
        positive=True,
    )
    lanes = {name: _lane(report, name) for name in LANES}
    outcome = _mapping(report.get("outcome"), "outcome")
    if outcome.get("equal") is not True:
        raise SameKernelPerformanceError("compat/native outcome is not equivalent")
    hashes = {
        lanes["compat"]["outcome_hash"],
        lanes["native"]["outcome_hash"],
        _integer(outcome.get("outcome_hash"), "outcome.outcome_hash"),
        _integer(outcome.get("compat_hash"), "outcome.compat_hash"),
        _integer(outcome.get("native_hash"), "outcome.native_hash"),
    }
    if len(hashes) != 1:
        raise SameKernelPerformanceError("outcome hashes do not match")
    mechanisms = _mechanisms(report)
    protocol_value = protocol if protocol is not None else report.get(
        "measurement_protocol"
    )
    supplied_protocol = _protocol(protocol_value)
    # Schema 4 predates the replayable evidence contract.  Its protocol is
    # useful diagnostic metadata, but cannot attest how the numbers were made.
    normalized_protocol = {
        **supplied_protocol,
        "declared": False,
        "cache_state": "uncontrolled",
        "quiescence_fence": False,
        "counter_isolation": "global_unisolated",
        "cow_exec_exercised_in_lanes": False,
    }
    counter_scope = mechanisms["compat"]["counter_scope"]
    if (
        normalized_protocol["counter_isolation"] == "scope_bound"
        and counter_scope == "global"
    ):
        raise SameKernelPerformanceError(
            "scope_bound protocol cannot use global mechanism counters"
        )
    if (
        normalized_protocol["counter_isolation"].startswith("global_")
        and counter_scope != "global"
    ):
        raise SameKernelPerformanceError(
            "global counter protocol does not match mechanism counter scope"
        )
    recovery_core = {
        lane: lanes[lane]["duration_us"] for lane in LANES
    }
    end_to_end: dict[str, int] | None = None
    if all("end_to_end_duration_us" in lanes[lane] for lane in LANES):
        end_to_end = {
            lane: lanes[lane]["end_to_end_duration_us"] for lane in LANES
        }
    elif any("end_to_end_duration_us" in lanes[lane] for lane in LANES):
        raise SameKernelPerformanceError(
            "end-to-end duration must be recorded for both lanes"
        )
    issues: list[str] = []
    if not normalized_protocol["declared"]:
        issues.append("measurement protocol is not bound to this run")
    if normalized_protocol["cache_state"] == "uncontrolled":
        issues.append("cache state is uncontrolled")
    if not normalized_protocol["quiescence_fence"]:
        issues.append("no quiescence fence isolates asynchronous kernel work")
    if normalized_protocol["counter_isolation"] == "global_unisolated":
        issues.append("global mechanism counters can include unrelated work")
    if end_to_end is None:
        issues.append("end-to-end latency is not recorded separately")
    if not normalized_protocol["cow_exec_exercised_in_lanes"]:
        issues.append("COW and exec-cache counters are workflow-only evidence")
    qemu_boots = _integer(run.get("qemu_boots"), "run.qemu_boots", positive=True)
    if qemu_boots != 1:
        raise SameKernelPerformanceError(
            "each showcase summary must represent exactly one QEMU boot"
        )
    return {
        "run_id": run_id,
        "commit": commit,
        "qemu_boots": qemu_boots,
        "actor_pid": actor_pid,
        "corpus_records": corpus_records,
        "timed_scope": timed_scope,
        "outcome_hash": next(iter(hashes)),
        "protocol": normalized_protocol,
        "latency_us": {
            "recovery_core": {
                **recovery_core,
                "compat_over_native": _ratio(
                    recovery_core["compat"], recovery_core["native"]
                ),
            },
            "end_to_end": None
            if end_to_end is None
            else {
                **end_to_end,
                "compat_over_native": _ratio(
                    end_to_end["compat"], end_to_end["native"]
                ),
            },
        },
        "work": {
            field: {
                "compat": lanes["compat"][field],
                "native": lanes["native"][field],
                "compat_over_native": _ratio(
                    lanes["compat"][field], lanes["native"][field]
                ),
            }
            for field in ("workload_syscalls", "records_examined", "bytes_read")
        },
        "mechanisms": mechanisms,
        "formal_evidence": False,
        "issues": issues,
    }


def load_sample(
    summary_path: Path,
    protocol_path: Path | None = None,
    *,
    allow_uncontrolled_legacy: bool = False,
) -> dict[str, Any]:
    if not allow_uncontrolled_legacy:
        raise SameKernelPerformanceError(
            "schema 4 evidence is legacy and uncontrolled; pass "
            "allow_uncontrolled_legacy=True only for diagnostics"
        )
    try:
        summary_bytes = summary_path.read_bytes()
        report = strict_json_loads(summary_bytes)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SameKernelPerformanceError(
            f"cannot read showcase summary {summary_path}: {error}"
        ) from error
    if not isinstance(report, dict):
        raise SameKernelPerformanceError("showcase summary root must be an object")
    protocol: dict[str, Any] | None = None
    protocol_bytes: bytes | None = None
    if protocol_path is not None:
        try:
            protocol_bytes = protocol_path.read_bytes()
            loaded = strict_json_loads(protocol_bytes)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise SameKernelPerformanceError(
                f"cannot read measurement protocol {protocol_path}: {error}"
            ) from error
        protocol = _mapping(loaded, "measurement protocol")
        run = _mapping(report.get("run"), "run")
        _validate_protocol_binding(
            protocol,
            summary_path,
            hashlib.sha256(summary_bytes).hexdigest(),
            _string(run.get("id"), "run.id"),
        )
    normalized = normalize_report(report, protocol=protocol)
    try:
        if summary_path.read_bytes() != summary_bytes:
            raise SameKernelPerformanceError(
                "showcase summary changed during legacy validation"
            )
        if (
            protocol_path is not None
            and protocol_bytes is not None
            and protocol_path.read_bytes() != protocol_bytes
        ):
            raise SameKernelPerformanceError(
                "measurement protocol changed during legacy validation"
            )
    except OSError as error:
        raise SameKernelPerformanceError(
            f"cannot recheck legacy evidence: {error}"
        ) from error
    return normalized


def _counter_scopes(value: object, label: str) -> dict[str, str]:
    raw = _mapping(value, label)
    if raw != COUNTER_SCOPES:
        raise SameKernelPerformanceError(
            f"{label} must distinguish global and observer-process counters"
        )
    return dict(COUNTER_SCOPES)


def _campaign_protocol(value: object, qemu_boots: int) -> dict[str, Any]:
    raw = _mapping(value, "measurement_protocol")
    if _integer(raw.get("sample_count"), "protocol sample_count", positive=True) != qemu_boots:
        raise SameKernelPerformanceError("measurement_protocol does not bind samples")
    fence = _mapping(raw.get("quiescence_fence"), "protocol quiescence_fence")
    stable_rounds = _integer(
        fence.get("stable_rounds"), "protocol stable_rounds", positive=True
    )
    max_attempts = _integer(
        fence.get("max_attempts"), "protocol max_attempts", positive=True
    )
    if stable_rounds < 2 or max_attempts < stable_rounds:
        raise SameKernelPerformanceError("quiescence fence budget is invalid")
    if _integer(
        fence.get("converged_samples"),
        "protocol converged_samples",
        positive=True,
    ) != qemu_boots:
        raise SameKernelPerformanceError("quiescence fences did not converge for every sample")
    counter_scope = _string(fence.get("counter_scope"), "protocol counter_scope")
    scopes = _counter_scopes(raw.get("counter_scopes"), "protocol counter_scopes")
    if (
        counter_scope != "field_scoped"
        or _counter_scopes(
            fence.get("counter_scopes"), "protocol quiescence counter_scopes"
        )
        != scopes
    ):
        raise SameKernelPerformanceError("protocol counter scopes conflict")
    if raw.get("ordering") != "balanced_AB_BA":
        raise SameKernelPerformanceError("campaign ordering is not balanced AB/BA")
    if raw.get("cold_hot_meaning") != "first_or_second_lane_within_each_boot":
        raise SameKernelPerformanceError("campaign cache-order meaning is unsupported")
    if _integer(raw.get("qemu_jobs"), "protocol qemu_jobs", positive=True) != 1:
        raise SameKernelPerformanceError("formal campaign must use one isolated QEMU")
    if raw.get("host_concurrency") != "one_isolated_qemu_at_a_time":
        raise SameKernelPerformanceError("campaign host concurrency is not isolated")
    identity = _mapping(raw.get("observer_identity"), "protocol observer_identity")
    if identity != {
        "workflow": "pid_plus_nonzero_lifecycle_id_generation",
        "system_probe": "signed_bootstrap_pid_plus_lifecycle_0_0",
    }:
        raise SameKernelPerformanceError("campaign observer identity is unsupported")
    return {
        "stable_rounds": stable_rounds,
        "max_attempts": max_attempts,
        "counter_scope": counter_scope,
        "fence_counter_scope": scopes["default"],
        "counter_scopes": scopes,
    }


def _run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            *contest_demo.SAFE_GIT_CONFIG_ARGUMENTS,
            "-C",
            str(root),
            *args,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=contest_demo.controlled_git_environment(),
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SameKernelPerformanceError(
            f"git {' '.join(args)} failed while validating source receipt: {detail}"
        )
    return result.stdout


def _git_text(root: Path, *args: str) -> str:
    try:
        return _run_git(root, *args).decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise SameKernelPerformanceError("Git returned a non-UTF-8 identity") from error


def _source_repository(summary_path: Path) -> Path:
    root = Path(
        _git_text(summary_path.parent, "rev-parse", "--show-toplevel")
    ).resolve()
    try:
        summary_path.resolve().relative_to(root)
    except ValueError as error:
        raise SameKernelPerformanceError(
            "formal summary must reside inside its source Git worktree"
        ) from error
    return root


def _commit_blob_oids(
    repository: Path, commit: str, paths: list[str]
) -> dict[str, str]:
    raw = _run_git(
        repository,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
        *paths,
    )
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, object_type, raw_oid = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8", errors="strict")
            oid = raw_oid.decode("ascii", errors="strict")
        except (ValueError, UnicodeDecodeError) as error:
            raise SameKernelPerformanceError("Git tree output is malformed") from error
        if (
            mode not in {b"100644", b"100755"}
            or object_type != b"blob"
            or path in result
            or not HEX_40.fullmatch(oid)
        ):
            raise SameKernelPerformanceError("showcase source tree is not a blob set")
        result[path] = oid
    if set(result) != set(paths):
        raise SameKernelPerformanceError(
            "specified commit does not contain the exact showcase source set"
        )
    return result


def _read_commit_blobs(repository: Path, oids: list[str]) -> dict[str, bytes]:
    process = subprocess.Popen(
        [
            "git",
            *contest_demo.SAFE_GIT_CONFIG_ARGUMENTS,
            "-C",
            str(repository),
            "cat-file",
            "--batch",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=contest_demo.controlled_git_environment(),
    )
    output, error = process.communicate(
        b"".join(oid.encode("ascii") + b"\n" for oid in oids)
    )
    if process.returncode != 0:
        raise SameKernelPerformanceError(
            "git cat-file --batch failed while validating source receipt: "
            + error.decode("utf-8", errors="replace").strip()
        )
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected_oid in oids:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise SameKernelPerformanceError("Git blob batch output is truncated")
        header = output[offset:header_end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise SameKernelPerformanceError("Git source object is not a blob")
        try:
            actual_oid = header[0].decode("ascii", errors="strict")
            size = int(header[2])
        except (UnicodeDecodeError, ValueError) as error:
            raise SameKernelPerformanceError("Git blob batch header is invalid") from error
        start = header_end + 1
        end = start + size
        if actual_oid != expected_oid or output[end:end + 1] != b"\n":
            raise SameKernelPerformanceError("Git blob batch output is inconsistent")
        blobs[expected_oid] = output[start:end]
        offset = end + 1
    if offset != len(output):
        raise SameKernelPerformanceError("Git blob batch output has trailing data")
    return blobs


def _validate_source_receipt(
    value: object, summary_path: Path, commit: str
) -> dict[str, Any]:
    receipt = _mapping(value, "source_receipt")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "agentos-showcase-source-receipt"
    ):
        raise SameKernelPerformanceError("unsupported source_receipt")
    sources = receipt.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SameKernelPerformanceError("source_receipt.sources must be non-empty")
    expected_paths = list(contest_demo.DEMO_SOURCE_PATHS)
    received_paths: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, value in enumerate(sources, 1):
        source = _mapping(value, f"source_receipt.sources[{index}]")
        if set(source) != {"path", "bytes", "sha256", "git_oid"}:
            raise SameKernelPerformanceError("source_receipt source shape is invalid")
        path = _string(source.get("path"), "source_receipt path")
        path_value = Path(path)
        if (
            path_value.is_absolute()
            or ".." in path_value.parts
            or path in received_paths
        ):
            raise SameKernelPerformanceError("source_receipt paths are not canonical")
        received_paths.append(path)
        size = _integer(source.get("bytes"), "source_receipt bytes")
        digest = _string(source.get("sha256"), "source_receipt sha256")
        oid = _string(source.get("git_oid"), "source_receipt git_oid")
        if not HEX_64.fullmatch(digest):
            raise SameKernelPerformanceError("source_receipt sha256 is invalid")
        if not HEX_40.fullmatch(oid):
            raise SameKernelPerformanceError("source_receipt git_oid is invalid")
        validated.append(
            {"path": path, "bytes": size, "sha256": digest, "git_oid": oid}
        )
    if received_paths != expected_paths:
        raise SameKernelPerformanceError(
            "source_receipt must contain the exact ordered showcase source set"
        )

    repository = _source_repository(summary_path)
    resolved_commit = _git_text(
        repository, "rev-parse", "--verify", f"{commit}^{{commit}}"
    )
    if resolved_commit != commit:
        raise SameKernelPerformanceError("campaign commit is not the specified commit object")
    tree_oids = _commit_blob_oids(repository, commit, expected_paths)
    blobs = _read_commit_blobs(repository, list(tree_oids.values()))
    for source in validated:
        path = source["path"]
        oid = tree_oids[path]
        if oid != source["git_oid"]:
            raise SameKernelPerformanceError(
                f"source_receipt git_oid does not match commit blob for {path}"
            )
        blob = blobs[oid]
        if (
            len(blob) != source["bytes"]
            or hashlib.sha256(blob).hexdigest() != source["sha256"]
        ):
            raise SameKernelPerformanceError(
                f"source_receipt bytes do not match commit blob for {path}"
            )
    return receipt


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def _file_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_size


def _artifact_snapshot(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink():
        raise SameKernelPerformanceError(f"artifact {name} must not be a symlink")
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise SameKernelPerformanceError(f"artifact {name} is missing")
        digest = hashlib.sha256()
        captured: list[bytes] | None = [] if name.endswith("-qemu.log") else None
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if _file_identity(opened) != _file_identity(before):
                raise SameKernelPerformanceError(
                    f"artifact {name} changed while it was opened"
                )
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                if captured is not None:
                    captured.append(chunk)
            after_read = os.fstat(source.fileno())
        after = path.stat(follow_symlinks=False)
    except OSError as error:
        raise SameKernelPerformanceError(
            f"cannot snapshot artifact {name}: {error}"
        ) from error
    identity = _stat_identity(before)
    if _stat_identity(after_read) != identity or _stat_identity(after) != identity:
        raise SameKernelPerformanceError(f"artifact {name} changed while hashing")
    return {
        "path": path,
        "bytes": before.st_size,
        "sha256": digest.hexdigest(),
        "identity": identity,
        "guest_bytes": b"".join(captured) if captured is not None else None,
    }


def _assert_artifact_stable(name: str, expected: dict[str, Any]) -> None:
    current = _artifact_snapshot(expected["path"], name)
    if (
        current["bytes"] != expected["bytes"]
        or current["sha256"] != expected["sha256"]
        or current["identity"] != expected["identity"]
    ):
        raise SameKernelPerformanceError(
            f"artifact {name} changed during formal evidence validation"
        )


def _validate_artifacts(value: object, root: Path, qemu_boots: int) -> dict[str, Any]:
    artifacts = _mapping(value, "artifacts")
    if not artifacts:
        raise SameKernelPerformanceError("artifacts must be non-empty")
    qemu_logs = 0
    snapshots: dict[str, Any] = {}
    for name, value in artifacts.items():
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise SameKernelPerformanceError("artifact names must be plain file names")
        record = _mapping(value, f"artifacts.{name}")
        if set(record) != {"bytes", "sha256"}:
            raise SameKernelPerformanceError(f"artifact {name} shape is invalid")
        size = _integer(record.get("bytes"), f"artifact {name} bytes")
        digest = _string(record.get("sha256"), f"artifact {name} sha256")
        if not HEX_64.fullmatch(digest):
            raise SameKernelPerformanceError(f"artifact {name} sha256 is invalid")
        snapshot = _artifact_snapshot(root / name, name)
        if snapshot["bytes"] != size or snapshot["sha256"] != digest:
            raise SameKernelPerformanceError(f"artifact {name} does not match its receipt")
        snapshots[name] = snapshot
        qemu_logs += name.endswith("-qemu.log")
    if qemu_logs != qemu_boots:
        raise SameKernelPerformanceError("artifacts do not bind every QEMU boot")
    return snapshots


def _validate_build_manifest(
    value: object,
    artifacts: dict[str, Any],
    run_id: str,
    commit: str,
    raw_samples: list[object],
    qemu_boots: int,
) -> list[dict[str, Any]]:
    manifest = _mapping(value, "build_manifest")
    if set(manifest) != {
        "schema_version",
        "kind",
        "run_id",
        "source_commit",
        "kernel_artifact",
        "samples",
    }:
        raise SameKernelPerformanceError("build_manifest shape is invalid")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "agentos-showcase-build-manifest"
        or manifest.get("run_id") != run_id
        or manifest.get("source_commit") != commit
        or manifest.get("kernel_artifact") != "showcase-kernel"
    ):
        raise SameKernelPerformanceError("build_manifest identity does not match campaign")
    rows = manifest.get("samples")
    if not isinstance(rows, list) or len(rows) != qemu_boots:
        raise SameKernelPerformanceError("build_manifest does not bind every sample")
    expected_artifacts = {"showcase-kernel"}
    validated: list[dict[str, Any]] = []
    for index, (value, raw_sample) in enumerate(zip(rows, raw_samples), 1):
        row = _mapping(value, f"build_manifest.samples[{index}]")
        if set(row) != {
            "sample_id",
            "order",
            "kernel_artifact",
            "guest_elf_artifact",
            "fs_image_artifact",
            "guest_log_artifact",
        }:
            raise SameKernelPerformanceError("build_manifest sample shape is invalid")
        sample = _mapping(raw_sample, f"samples[{index}]")
        sample_record = _mapping(sample.get("sample"), f"samples[{index}].sample")
        expected = {
            "sample_id": index,
            "order": sample_record.get("order"),
            "kernel_artifact": "showcase-kernel",
            "guest_elf_artifact": f"sample-{index:02d}-labdemo.elf",
            "fs_image_artifact": f"sample-{index:02d}-fs.img",
            "guest_log_artifact": f"sample-{index:02d}-qemu.log",
        }
        if row != expected:
            raise SameKernelPerformanceError(
                f"build_manifest sample {index} does not match campaign"
            )
        expected_artifacts.update(
            {
                expected["guest_elf_artifact"],
                expected["fs_image_artifact"],
                expected["guest_log_artifact"],
            }
        )
        validated.append(row)
    if set(artifacts) != expected_artifacts:
        raise SameKernelPerformanceError(
            "artifacts must exactly match the build_manifest inventory"
        )
    return validated


def _validate_fence_stream(
    value: object,
    label: str,
    points: tuple[str, ...],
    protocol: dict[str, Any],
    *,
    workflow_observer: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(points):
        raise SameKernelPerformanceError(f"{label} fences are incomplete")
    rows: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for sequence, (value, point) in enumerate(zip(value, points), 1):
        raw = _mapping(value, f"{label} fences[{sequence}]")
        row = {
            "sequence": _integer(raw.get("sequence"), f"{label} sequence", positive=True),
            "point": _string(raw.get("point"), f"{label} point"),
            "tick_us": _integer(raw.get("tick_us"), f"{label} tick_us"),
            "attempts": _integer(raw.get("attempts"), f"{label} attempts", positive=True),
            "stable_rounds": _integer(
                raw.get("stable_rounds"), f"{label} stable_rounds", positive=True
            ),
            "observer_pid": _integer(
                raw.get("observer_pid"), f"{label} observer_pid", positive=True
            ),
            "observer_tick": _integer(
                raw.get("observer_tick"), f"{label} observer_tick"
            ),
            "observer_lifecycle_id": _integer(
                raw.get("observer_lifecycle_id"), f"{label} lifecycle id"
            ),
            "observer_lifecycle_generation": _integer(
                raw.get("observer_lifecycle_generation"),
                f"{label} lifecycle generation",
            ),
            "counter_scope": _string(
                raw.get("counter_scope"), f"{label} counter_scope"
            ),
        }
        for field in FENCE_COUNTER_FIELDS:
            row[field] = _integer(raw.get(field), f"{label} {field}")
        if row["sequence"] != sequence or row["point"] != point:
            raise SameKernelPerformanceError(f"{label} fences are out of order")
        if (
            row["stable_rounds"] != protocol["stable_rounds"]
            or row["attempts"] < row["stable_rounds"]
            or row["attempts"] > protocol["max_attempts"]
            or row["counter_scope"] != protocol["fence_counter_scope"]
            or row["metadata_pending"] != 0
            or row["metadata_dirty"] != row["metadata_durable"]
        ):
            raise SameKernelPerformanceError(f"{label} fence is not quiescent")
        lifecycle = (
            row["observer_lifecycle_id"],
            row["observer_lifecycle_generation"],
        )
        if (workflow_observer and (0 in lifecycle)) or (
            not workflow_observer and lifecycle != (0, 0)
        ):
            raise SameKernelPerformanceError(f"{label} observer identity is invalid")
        if previous is not None and (
            row["tick_us"] < previous["tick_us"]
            or row["observer_tick"] <= previous["observer_tick"]
            or any(
                row[key] != previous[key]
                for key in (
                    "observer_pid",
                    "observer_lifecycle_id",
                    "observer_lifecycle_generation",
                )
            )
            or any(row[field] < previous[field] for field in FENCE_COUNTER_FIELDS)
        ):
            raise SameKernelPerformanceError(f"{label} fence stream is not monotonic")
        rows.append(row)
        previous = row
    return rows


def _raw_pair(value: object, label: str) -> tuple[dict[str, int], dict[str, int]]:
    pair = _mapping(value, f"{label}.raw_pair")
    if set(pair) != {"before", "after"}:
        raise SameKernelPerformanceError(f"{label}.raw_pair shape is invalid")
    result: list[dict[str, int]] = []
    for endpoint in ("before", "after"):
        raw = _mapping(pair.get(endpoint), f"{label}.raw_pair.{endpoint}")
        if set(raw) != set(RAW_MECHANISM_FIELDS):
            raise SameKernelPerformanceError(f"{label}.raw_pair.{endpoint} is incomplete")
        result.append(
            {
                field: _integer(raw.get(field), f"{label} {endpoint} {field}")
                for field in RAW_MECHANISM_FIELDS
            }
        )
    before, after = result
    if any(after[field] < before[field] for field in RAW_MECHANISM_FIELDS):
        raise SameKernelPerformanceError(f"{label}.raw_pair is not monotonic")
    return before, after


def _validate_mechanism_receipt(
    value: object,
    label: str,
    protocol: dict[str, Any],
    *,
    before_fence: dict[str, Any] | None = None,
    after_fence: dict[str, Any] | None = None,
    exact_after: bool = True,
) -> dict[str, Any]:
    item = _mapping(value, label)
    if _counter_scopes(item.get("counter_scopes"), f"{label}.counter_scopes") != protocol[
        "counter_scopes"
    ]:
        raise SameKernelPerformanceError(f"{label} counter scopes conflict")
    if _string(item.get("counter_scope"), f"{label} counter_scope") != protocol[
        "fence_counter_scope"
    ]:
        raise SameKernelPerformanceError(f"{label} counter scope conflicts")
    before, after = _raw_pair(item.get("raw_pair"), label)
    for field in RAW_MECHANISM_FIELDS:
        if _integer(item.get(field), f"{label} {field}") != after[field] - before[field]:
            raise SameKernelPerformanceError(f"{label} does not match raw_pair")
    before_tick = _integer(item.get("before_tick"), f"{label} before_tick")
    after_tick = _integer(item.get("after_tick"), f"{label} after_tick")
    if after_tick <= before_tick:
        raise SameKernelPerformanceError(f"{label} ticks are not increasing")
    identity = (
        _integer(item.get("observer_pid"), f"{label} observer_pid", positive=True),
        _integer(item.get("observer_lifecycle_id"), f"{label} lifecycle id"),
        _integer(
            item.get("observer_lifecycle_generation"), f"{label} lifecycle generation"
        ),
    )
    before_pending = _integer(
        item.get("before_metadata_pending"), f"{label} before_metadata_pending"
    )
    after_pending = _integer(
        item.get("after_metadata_pending"), f"{label} after_metadata_pending"
    )
    if before_fence is not None and after_fence is not None:
        expected_identity = (
            before_fence["observer_pid"],
            before_fence["observer_lifecycle_id"],
            before_fence["observer_lifecycle_generation"],
        )
        if identity != expected_identity or identity != (
            after_fence["observer_pid"],
            after_fence["observer_lifecycle_id"],
            after_fence["observer_lifecycle_generation"],
        ):
            raise SameKernelPerformanceError(f"{label} observer does not match fences")
        if before_tick != before_fence["observer_tick"] or (
            after_tick != after_fence["observer_tick"]
            if exact_after
            else after_tick > after_fence["observer_tick"]
        ):
            raise SameKernelPerformanceError(f"{label} ticks do not match fences")
        if before_pending != before_fence["metadata_pending"] or (
            after_pending != after_fence["metadata_pending"]
            if exact_after
            else after_pending > after_fence["metadata_pending"]
        ):
            raise SameKernelPerformanceError(f"{label} pending state does not match fences")
        for field in FENCE_COUNTER_FIELDS:
            if field == "metadata_pending":
                continue
            if before[field] != before_fence[field] or (
                after[field] != after_fence[field]
                if exact_after
                else after[field] > after_fence[field]
            ):
                raise SameKernelPerformanceError(f"{label} raw_pair does not match fences")
    normalized = _mechanisms(
        {"mechanisms": {mode: item for mode in MECHANISM_MODES}}
    )["compat"]
    normalized["counter_scopes"] = dict(protocol["counter_scopes"])
    normalized["observer_pid"] = identity[0]
    normalized["observer_lifecycle_id"] = identity[1]
    normalized["observer_lifecycle_generation"] = identity[2]
    normalized["raw_pair"] = {"before": before, "after": after}
    return normalized


def _campaign_samples(
    report: dict[str, Any], summary_path: Path
) -> list[dict[str, Any]]:
    if report.get("kind") != REPORT_KIND or report.get("schema_version") != 6:
        raise SameKernelPerformanceError("unsupported contest showcase campaign")
    run = _mapping(report.get("run"), "run")
    qemu_boots = _integer(run.get("qemu_boots"), "run.qemu_boots", positive=True)
    raw_samples = report.get("samples")
    if (
        not isinstance(raw_samples, list)
        or len(raw_samples) != qemu_boots
        or qemu_boots < MIN_CONTROLLED_RUNS
        or qemu_boots % 2 != 0
    ):
        raise SameKernelPerformanceError("campaign samples do not bind QEMU boots")
    outer_id = _string(run.get("id"), "run.id")
    if not re.fullmatch(r"[0-9a-f]{16}", outer_id) or int(outer_id, 16) == 0:
        raise SameKernelPerformanceError("campaign run.id is invalid")
    commit = _string(run.get("commit"), "run.commit")
    if not HEX_40.fullmatch(commit):
        raise SameKernelPerformanceError("campaign commit is not a full object id")
    protocol = _campaign_protocol(report.get("measurement_protocol"), qemu_boots)
    source_receipt = _validate_source_receipt(
        report.get("source_receipt"), summary_path, commit
    )
    artifacts = _validate_artifacts(
        report.get("artifacts"), summary_path.parent, qemu_boots
    )
    manifest = _validate_build_manifest(
        report.get("build_manifest"),
        artifacts,
        outer_id,
        commit,
        raw_samples,
        qemu_boots,
    )
    replayed_samples: list[dict[str, Any]] = []
    for index, (raw, manifest_row) in enumerate(zip(raw_samples, manifest), 1):
        log_name = manifest_row["guest_log_artifact"]
        _assert_artifact_stable(log_name, artifacts[log_name])
        try:
            replayed = contest_demo.verify_showcase(
                artifacts[log_name]["path"],
                outer_id,
                index,
                manifest_row["order"],
                guest_bytes=artifacts[log_name]["guest_bytes"],
            )
        except (contest_demo.ContestDemoError, OSError) as error:
            raise SameKernelPerformanceError(
                f"raw Guest log replay failed for sample {index}: {error}"
            ) from error
        _assert_artifact_stable(log_name, artifacts[log_name])
        if replayed != raw:
            raise SameKernelPerformanceError(
                f"sample {index} does not match its raw Guest log replay"
            )
        replayed_samples.append(replayed)
    try:
        expected_aggregates = contest_demo.campaign_aggregates(replayed_samples)
    except contest_demo.ContestDemoError as error:
        raise SameKernelPerformanceError(
            f"replayed campaign aggregates are invalid: {error}"
        ) from error
    for field, expected in expected_aggregates.items():
        if report.get(field) != expected:
            raise SameKernelPerformanceError(
                f"campaign {field} does not match replayed Guest evidence"
            )
    outer_comparison = _mapping(report.get("comparison"), "comparison")
    if outer_comparison.get("design") != "same_kernel_same_guest_same_corpus":
        raise SameKernelPerformanceError("comparison is not a same-kernel design")
    timed_scopes = _mapping(outer_comparison.get("timed_scope"), "comparison.timed_scope")
    if timed_scopes != {
        "core": "incident_to_verified_durable_outcome",
        "end_to_end": "quiescent_seed_core_cleanup_quiescent",
    }:
        raise SameKernelPerformanceError("campaign timed scopes are unsupported")
    corpus_records = _integer(
        outer_comparison.get("corpus_records"),
        "comparison.corpus_records",
        positive=True,
    )
    outer_outcome = _mapping(report.get("outcome"), "outcome")
    expected_orders = _mapping(
        outer_outcome.get("execution_orders"), "outcome.execution_orders"
    )
    normalized: list[dict[str, Any]] = []
    order_counts = {"compat_then_native": 0, "native_then_compat": 0}
    for index, raw in enumerate(raw_samples, 1):
        sample = _mapping(raw, f"samples[{index}]")
        sample_record = _mapping(sample.get("sample"), f"samples[{index}].sample")
        if _integer(sample_record.get("id"), "sample id", positive=True) != index:
            raise SameKernelPerformanceError("campaign sample order is not canonical")
        order = _string(sample_record.get("order"), "sample order")
        if order not in order_counts:
            raise SameKernelPerformanceError("campaign lane order is invalid")
        order_counts[order] += 1
        comparison = _mapping(sample.get("comparison"), "sample comparison")
        mechanisms = _mapping(sample.get("mechanisms"), "sample mechanisms")
        if set(mechanisms) != set(CAMPAIGN_MECHANISM_MODES):
            raise SameKernelPerformanceError(
                "sample mechanisms must contain compat, native, workflow, and runtime_probe"
            )
        lanes = _mapping(comparison.get("lanes"), "sample lanes")
        outcome = _mapping(sample.get("outcome"), "sample outcome")
        if comparison.get("design") != outer_comparison.get("design"):
            raise SameKernelPerformanceError("sample comparison design changed")
        if comparison.get("timed_scope") != timed_scopes["core"]:
            raise SameKernelPerformanceError("sample core timed scope changed")
        if comparison.get("corpus_records") != corpus_records:
            raise SameKernelPerformanceError("sample corpus changed")
        if outcome.get("execution_order") != order:
            raise SameKernelPerformanceError("sample outcome order does not match sample")
        sample_outcome = dict(outcome)
        sample_outcome.pop("execution_order", None)
        campaign_outcome = dict(outer_outcome)
        campaign_outcome.pop("execution_orders", None)
        if sample_outcome != campaign_outcome:
            raise SameKernelPerformanceError("sample outcome differs from campaign outcome")

        raw_fences = _mapping(sample.get("fences"), "sample fences")
        if set(raw_fences) != {"compat", "native", "runtime_probe"}:
            raise SameKernelPerformanceError("sample fences are incomplete")
        fences = {
            mode: _validate_fence_stream(
                raw_fences.get(mode),
                f"sample {index} {mode}",
                ("E2E_START", "CORE_START", "ACK_SETTLED", "E2E_END"),
                protocol,
                workflow_observer=True,
            )
            for mode in LANES
        }
        fences["runtime_probe"] = _validate_fence_stream(
            raw_fences.get("runtime_probe"),
            f"sample {index} runtime_probe",
            ("PROBE_START", "PROBE_END"),
            protocol,
            workflow_observer=False,
        )
        expected_lane_order = order.replace("_then_", " ").split()
        first_fences = fences[expected_lane_order[0]]
        second_fences = fences[expected_lane_order[1]]
        if first_fences[-1]["tick_us"] > second_fences[0]["tick_us"]:
            raise SameKernelPerformanceError("sample fence order contradicts AB/BA order")
        actor_pid = _integer(
            comparison.get("execution_actor_pid"),
            "sample execution_actor_pid",
            positive=True,
        )
        lane_identity = (
            actor_pid,
            fences["compat"][0]["observer_lifecycle_id"],
            fences["compat"][0]["observer_lifecycle_generation"],
        )
        for mode in LANES:
            if any(
                (
                    row["observer_pid"],
                    row["observer_lifecycle_id"],
                    row["observer_lifecycle_generation"],
                )
                != lane_identity
                for row in fences[mode]
            ):
                raise SameKernelPerformanceError("sample workload observer changed")

        normalized_intervals: dict[str, dict[str, Any]] = {}
        for mode in LANES:
            mode_records = _mapping(mechanisms.get(mode), f"mechanisms.{mode}")
            if set(mode_records) != {"core", "end_to_end"}:
                raise SameKernelPerformanceError(f"{mode} mechanism intervals are incomplete")
            _validate_mechanism_receipt(
                mode_records.get("core"),
                f"sample {index} {mode}.core",
                protocol,
                before_fence=fences[mode][1],
                after_fence=fences[mode][2],
                exact_after=False,
            )
            normalized_intervals[mode] = _validate_mechanism_receipt(
                mode_records.get("end_to_end"),
                f"sample {index} {mode}.end_to_end",
                protocol,
                before_fence=fences[mode][0],
                after_fence=fences[mode][3],
            )
        workflow_records = _mapping(mechanisms.get("workflow"), "mechanisms.workflow")
        probe_records = _mapping(
            mechanisms.get("runtime_probe"), "mechanisms.runtime_probe"
        )
        if set(workflow_records) != {"end_to_end"} or set(probe_records) != {
            "end_to_end"
        }:
            raise SameKernelPerformanceError("supporting mechanism intervals are incomplete")
        workflow = _validate_mechanism_receipt(
            workflow_records.get("end_to_end"),
            f"sample {index} workflow.end_to_end",
            protocol,
        )
        if (
            workflow["observer_pid"],
            workflow["observer_lifecycle_id"],
            workflow["observer_lifecycle_generation"],
        ) != lane_identity:
            raise SameKernelPerformanceError("workflow mechanism observer changed")
        runtime_probe = _validate_mechanism_receipt(
            probe_records.get("end_to_end"),
            f"sample {index} runtime_probe.end_to_end",
            protocol,
            before_fence=fences["runtime_probe"][0],
            after_fence=fences["runtime_probe"][1],
        )
        if (
            runtime_probe["observer_pid"] == actor_pid
            or runtime_probe["observer_lifecycle_id"] != 0
            or runtime_probe["observer_lifecycle_generation"] != 0
        ):
            raise SameKernelPerformanceError("runtime_probe observer is not isolated")
        normalized_intervals["workflow"] = workflow
        normalized_intervals["runtime_probe"] = runtime_probe

        legacy = {
            "schema_version": 4,
            "kind": REPORT_KIND,
            "run": {
                "id": f"{outer_id}-{index:02d}",
                "commit": commit,
                "qemu_boots": 1,
            },
            "comparison": {
                "design": outer_comparison["design"],
                "execution_actor_pid": actor_pid,
                "timed_scope": timed_scopes["core"],
                "corpus_records": corpus_records,
                "lanes": {
                    mode: {
                        "duration_us": _mapping(lanes.get(mode), mode).get(
                            "core_duration_us"
                        ),
                        "end_to_end_duration_us": _mapping(
                            lanes.get(mode), mode
                        ).get("end_to_end_duration_us"),
                        **{
                            field: _mapping(lanes.get(mode), mode).get(field)
                            for field in (
                                "workload_syscalls", "records_examined", "bytes_read",
                                "result_items", "outcome_hash",
                            )
                        },
                    }
                    for mode in LANES
                },
            },
            "outcome": outcome,
            "mechanisms": {
                mode: normalized_intervals[mode]
                for mode in MECHANISM_MODES
            },
            "measurement_protocol": {
                "schema_version": 1,
                "kind": PROTOCOL_KIND,
                "cache_state": "order_counterbalanced",
                "lane_order": expected_lane_order,
                "setup_included": True,
                "quiescence_fence": protocol["stable_rounds"] >= 2,
                "counter_isolation": (
                    "global_quiesced"
                    if protocol["fence_counter_scope"] == "global"
                    else "scope_bound"
                ),
                "cow_exec_exercised_in_lanes": any(
                    normalized_intervals[mode][field] > 0
                    for mode in LANES
                    for field, _unit in WORKFLOW_FIELDS
                ),
            },
        }
        normalized_sample = normalize_report(legacy)
        formal_protocol = _protocol(legacy["measurement_protocol"])
        formal_protocol["counter_scopes"] = dict(protocol["counter_scopes"])
        normalized_sample["protocol"] = formal_protocol
        normalized_sample["formal_evidence"] = True
        normalized_sample["issues"] = []
        if not formal_protocol["cow_exec_exercised_in_lanes"]:
            normalized_sample["issues"].append(
                "COW and exec-cache counters are workflow-only evidence"
            )
        for mode in MECHANISM_MODES:
            normalized_sample["mechanisms"][mode]["counter_scopes"] = dict(
                protocol["counter_scopes"]
            )
        normalized_sample["mechanisms"]["runtime_probe"] = runtime_probe
        normalized_sample["timed_scopes"] = dict(timed_scopes)
        normalized_sample["campaign_receipt"] = {
            "source_count": len(source_receipt["sources"]),
            "artifact_count": len(artifacts),
            "build_manifest_kind": "agentos-showcase-build-manifest",
            "raw_guest_log_replayed": True,
        }
        normalized.append(normalized_sample)
    if len(set(order_counts.values())) != 1:
        raise SameKernelPerformanceError("campaign is not AB/BA balanced")
    if order_counts != expected_orders or outer_comparison.get("order_balance") != order_counts:
        raise SameKernelPerformanceError("campaign AB/BA receipts disagree")
    for name, snapshot in artifacts.items():
        _assert_artifact_stable(name, snapshot)
    return normalized


def load_samples(
    summary_path: Path,
    protocol_path: Path | None = None,
    *,
    allow_uncontrolled_legacy: bool = False,
) -> list[dict[str, Any]]:
    try:
        if summary_path.is_symlink():
            raise SameKernelPerformanceError(
                "formal summary must not be a symlink"
            )
        summary_bytes = summary_path.read_bytes()
        report = strict_json_loads(summary_bytes)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SameKernelPerformanceError(
            f"cannot read showcase summary {summary_path}: {error}"
        ) from error
    if not isinstance(report, dict):
        raise SameKernelPerformanceError("showcase summary root must be an object")
    if report.get("schema_version") == 6:
        if protocol_path is not None:
            raise SameKernelPerformanceError(
                "campaign summaries already contain their measurement protocol"
            )
        samples = _campaign_samples(report, summary_path)
        try:
            if summary_path.read_bytes() != summary_bytes:
                raise SameKernelPerformanceError(
                    "showcase summary changed during formal validation"
                )
        except OSError as error:
            raise SameKernelPerformanceError(
                f"cannot recheck showcase summary {summary_path}: {error}"
            ) from error
        return samples
    if not allow_uncontrolled_legacy:
        raise SameKernelPerformanceError(
            "formal evidence requires a replayable schema 6 campaign; "
            "legacy schema 4 is diagnostic only"
        )
    return [
        load_sample(
            summary_path,
            protocol_path,
            allow_uncontrolled_legacy=True,
        )
    ]


def _percentile95(values: list[float | int]) -> float | int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _stats(values: Iterable[float | int | None]) -> dict[str, float | int] | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    p50 = median(usable)
    return {
        "count": len(usable),
        "min": min(usable),
        "p50": p50,
        "p95": _percentile95(usable),
        "max": max(usable),
    }


def _mean_ci95(values: Iterable[float | int | None]) -> dict[str, float] | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    estimate = sum(usable) / len(usable)
    if len(usable) == 1:
        margin = 0.0
    else:
        variance = sum((value - estimate) ** 2 for value in usable) / (
            len(usable) - 1
        )
        critical = {
            7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
            11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
            15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
            19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
            23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
            27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
        }.get(len(usable) - 1, 1.96)
        margin = critical * (variance / len(usable)) ** 0.5
    return {
        "estimate": round(estimate, 6),
        "low": round(estimate - margin, 6),
        "high": round(estimate + margin, 6),
    }


def _paired_stats(
    samples: list[dict[str, Any]], getter: Any
) -> dict[str, object]:
    pairs = [getter(sample) for sample in samples]
    return {
        "compat": _stats(pair[0] for pair in pairs),
        "native": _stats(pair[1] for pair in pairs),
        "compat_over_native": _stats(_ratio(pair[0], pair[1]) for pair in pairs),
        "native_minus_compat_ci95": _mean_ci95(
            pair[1] - pair[0] for pair in pairs
        ),
        "compat_over_native_ci95": _mean_ci95(
            _ratio(pair[0], pair[1]) for pair in pairs
        ),
    }


def _group_key(sample: dict[str, Any]) -> tuple[object, ...]:
    protocol = sample["protocol"]
    counter_scopes = protocol.get(
        "counter_scopes", {"default": sample["mechanisms"]["compat"]["counter_scope"]}
    )
    return (
        sample["commit"],
        sample["corpus_records"],
        sample["timed_scope"],
        sample["outcome_hash"],
        protocol["cache_state"],
        protocol["setup_included"],
        protocol["quiescence_fence"],
        protocol["counter_isolation"],
        protocol["cow_exec_exercised_in_lanes"],
        tuple(sorted(counter_scopes.items())),
        sample["mechanisms"]["compat"]["counter_scope"],
        sample["latency_us"]["end_to_end"] is not None,
        sample.get("formal_evidence") is True,
    )


def _aggregate_group(samples: list[dict[str, Any]], index: int) -> dict[str, Any]:
    first = samples[0]
    orders = [tuple(sample["protocol"]["lane_order"]) for sample in samples]
    order_counts = {
        "compat_native": orders.count(("compat", "native")),
        "native_compat": orders.count(("native", "compat")),
    }
    core_reasons: list[str] = []
    formal_evidence = all(
        sample.get("formal_evidence") is True for sample in samples
    )
    if not formal_evidence:
        core_reasons.append("input is not replay-verified formal evidence")
    if len(samples) < MIN_CONTROLLED_RUNS:
        core_reasons.append(f"requires at least {MIN_CONTROLLED_RUNS} QEMU runs")
    if any(not sample["protocol"]["declared"] for sample in samples):
        core_reasons.append("measurement protocol is undeclared")
    if first["protocol"]["cache_state"] == "uncontrolled":
        core_reasons.append("cache state is uncontrolled")
    if min(order_counts.values()) == 0 or abs(
        order_counts["compat_native"] - order_counts["native_compat"]
    ) > 1:
        core_reasons.append("lane order is not counterbalanced")
    if any(not sample["protocol"]["quiescence_fence"] for sample in samples):
        core_reasons.append("kernel background work is not fenced")
    e2e_reasons = list(core_reasons)
    if any(sample["latency_us"]["end_to_end"] is None for sample in samples):
        e2e_reasons.append("end-to-end latency is missing")
    mechanism_reasons = list(core_reasons)
    if any(
        sample["protocol"]["counter_isolation"]
        not in {"scope_bound", "global_quiesced"}
        for sample in samples
    ):
        mechanism_reasons.append("mechanism counters are not isolated")
    cow_exec_reasons = list(mechanism_reasons)
    if any(
        not sample["protocol"]["cow_exec_exercised_in_lanes"]
        for sample in samples
    ):
        cow_exec_reasons.append("COW/exec-cache are not exercised in both lanes")
    metrics: dict[str, Any] = {
        "recovery_core_latency_us": _paired_stats(
            samples,
            lambda sample: (
                sample["latency_us"]["recovery_core"]["compat"],
                sample["latency_us"]["recovery_core"]["native"],
            ),
        ),
        "end_to_end_latency_us": None,
    }
    if not any(sample["latency_us"]["end_to_end"] is None for sample in samples):
        metrics["end_to_end_latency_us"] = _paired_stats(
            samples,
            lambda sample: (
                sample["latency_us"]["end_to_end"]["compat"],
                sample["latency_us"]["end_to_end"]["native"],
            ),
        )
    for field, _unit in PAIR_FIELDS:
        metrics[field] = _paired_stats(
            samples,
            lambda sample, key=field: (
                sample["mechanisms"]["compat"][key],
                sample["mechanisms"]["native"][key],
            ),
        )
    workflow = {
        field: _stats(
            sample["mechanisms"]["workflow"][field] for sample in samples
        )
        for field, _unit in WORKFLOW_FIELDS
    }
    runtime_fields = tuple(
        dict.fromkeys(
            (*MECHANISM_FIELDS, *(field for field, _unit in PAIR_FIELDS))
        )
    )
    runtime_probe = (
        {
            field: _stats(
                sample["mechanisms"]["runtime_probe"][field] for sample in samples
            )
            for field in runtime_fields
        }
        if all("runtime_probe" in sample["mechanisms"] for sample in samples)
        else None
    )
    return {
        "id": f"group-{index}",
        "commit": first["commit"],
        "corpus_records": first["corpus_records"],
        "timed_scope": first["timed_scope"],
        "cache_state": first["protocol"]["cache_state"],
        "counter_scopes": first["protocol"].get(
            "counter_scopes",
            {"default": first["mechanisms"]["compat"]["counter_scope"]},
        ),
        "setup_included": first["protocol"]["setup_included"],
        "sample_count": len(samples),
        "formal_evidence": formal_evidence,
        "headline_eligible": formal_evidence and not core_reasons,
        "lane_order_counts": order_counts,
        "metrics": metrics,
        "workflow_mechanisms": workflow,
        "runtime_probe_mechanisms": runtime_probe,
        "claim_boundaries": {
            "recovery_core": {
                "evidence": "controlled_repeated" if not core_reasons else "diagnostic",
                "reasons": core_reasons,
            },
            "end_to_end": {
                "evidence": "controlled_repeated" if not e2e_reasons else "unavailable",
                "reasons": e2e_reasons,
            },
            "io_epoch": {
                "evidence": "controlled_repeated"
                if not mechanism_reasons
                else "diagnostic",
                "reasons": mechanism_reasons,
            },
            "cow_exec_cache": {
                "evidence": "lane_comparison"
                if not cow_exec_reasons
                else "workflow_only",
                "reasons": cow_exec_reasons,
            },
        },
    }


def build_dataset(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        raise SameKernelPerformanceError("at least one sample is required")
    run_ids = [sample["run_id"] for sample in samples]
    if len(set(run_ids)) != len(run_ids):
        raise SameKernelPerformanceError("duplicate run_id in comparison input")
    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(_group_key(sample), []).append(sample)
    groups = [
        _aggregate_group(group, index)
        for index, (_key, group) in enumerate(
            sorted(grouped.items(), key=lambda item: repr(item[0])), start=1
        )
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "sample_count": len(samples),
        "formal_evidence": all(
            sample.get("formal_evidence") is True for sample in samples
        ),
        "headline_eligible": bool(groups)
        and all(group["headline_eligible"] for group in groups),
        "cache_groups_are_separate": True,
        "samples": samples,
        "groups": groups,
    }


def _csv_rows(dataset: dict[str, Any]) -> Iterable[list[object]]:
    units = dict(PAIR_FIELDS)
    units.update({"recovery_core_latency_us": "us", "end_to_end_latency_us": "us"})
    for group in dataset["groups"]:
        for metric_name, metric in group["metrics"].items():
            if metric is None:
                yield [
                    group["id"], group["cache_state"], group["sample_count"],
                    metric_name, units[metric_name], "", "", "", "unavailable",
                    group["claim_boundaries"]["end_to_end"]["evidence"],
                    group["headline_eligible"],
                ]
                continue
            boundary = (
                "recovery_core"
                if metric_name == "recovery_core_latency_us"
                else "end_to_end"
                if metric_name == "end_to_end_latency_us"
                else "io_epoch"
            )
            yield [
                group["id"],
                group["cache_state"],
                group["sample_count"],
                metric_name,
                units[metric_name],
                metric["compat"]["p50"],
                metric["native"]["p50"],
                metric["compat_over_native"]["p50"]
                if metric["compat_over_native"] is not None
                else "",
                "compat_native",
                group["claim_boundaries"][boundary]["evidence"],
                group["headline_eligible"],
            ]
        workflow_units = dict(WORKFLOW_FIELDS)
        for metric_name, stats in group["workflow_mechanisms"].items():
            yield [
                group["id"], group["cache_state"], group["sample_count"],
                metric_name, workflow_units[metric_name], "", "", "",
                f"workflow_p50={stats['p50']}",
                group["claim_boundaries"]["cow_exec_cache"]["evidence"],
                group["headline_eligible"],
            ]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def write_outputs(dataset: dict[str, Any], output_dir: Path) -> None:
    serialized = json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(output_dir / "same-kernel-metrics.json", serialized.encode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="agentos-same-kernel-csv-") as temporary:
        csv_path = Path(temporary) / "metrics.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                [
                    "group_id", "cache_state", "sample_count", "metric", "unit",
                    "compat_p50", "native_p50", "compat_over_native_p50",
                    "attribution", "evidence", "headline_eligible",
                ]
            )
            writer.writerows(_csv_rows(dataset))
        _atomic_write(output_dir / "same-kernel-metrics.csv", csv_path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize numeric compat/native evidence from contest showcase runs."
    )
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--protocol", action="append", type=Path, default=[])
    parser.add_argument(
        "--allow-uncontrolled-legacy",
        action="store_true",
        help="accept schema 4 inputs as diagnostic-only, never headline evidence",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.protocol and len(args.protocol) != len(args.input):
        parser.error("--protocol must be omitted or supplied once per --input")
    protocols = args.protocol or [None] * len(args.input)
    try:
        samples = [
            sample
            for path, protocol in zip(args.input, protocols)
            for sample in load_samples(
                path,
                protocol,
                allow_uncontrolled_legacy=args.allow_uncontrolled_legacy,
            )
        ]
        dataset = build_dataset(samples)
        write_outputs(dataset, args.output_dir)
    except SameKernelPerformanceError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
