#!/usr/bin/env python3
"""Verify AgentOS Guest logs and summarize practical AB/BA measurements."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
KIND = "agentos-contest-demo-results"
UINT = r"(?:0|[1-9][0-9]*)"
UINT64_MAX = (1 << 64) - 1
CORPUS_SIZE = 96

LEGACY_PATTERNS = {
    "startup": re.compile(
        r"^labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3$"
    ),
    "audit": re.compile(
        r"^labdemo_ucore: global_audit=1 records=([1-9][0-9]*) "
        r"agents=3 context=1 event=1 sched=1 message=1$"
    ),
    "timeline": re.compile(
        r"^labdemo_ucore: unified_timeline records=([1-9][0-9]*) "
        r"context=1 event=1 sched=1 message=1$"
    ),
    "provenance": re.compile(
        r"^labdemo_ucore: provenance_graph edges=([1-9][0-9]*) "
        r"message=1 context=1$"
    ),
    "scenario": re.compile(r"^labdemo_ucore: passed$"),
    "parent": re.compile(r"^labdemo_ucore: parent passed$"),
}
EVENT_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=event "
    rf"mode=(compat|native) seq=({UINT}) tick_us=({UINT}) "
    rf"role=(orchestrator|sentinel|recovery) "
    rf"event=(INCIDENT|DISCOVERED|RECOVERY_COMMITTED|RECOVERED) "
    rf"value0=({UINT}) value1=({UINT})$"
)
RUN_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=run sample=({UINT}) "
    rf"order=(compat_then_native|native_then_compat)$"
)
METRIC_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=metric "
    rf"mode=(compat|native) actor_pid=({UINT}) core_duration_us=({UINT}) "
    rf"end_to_end_duration_us=({UINT}) end_to_end_started_us=({UINT}) "
    rf"end_to_end_finished_us=({UINT}) workload_syscalls=({UINT}) "
    rf"records_examined=({UINT}) bytes_read=({UINT}) "
    rf"result_items=({UINT}) outcome_hash=({UINT})$"
)

FENCE_PERFORMANCE_COUNTERS = (
    "epoch_commits",
    "epoch_buffers_staged",
    "physical_writes",
    "physical_reads",
    "durable_flushes",
    "deduplicated_stages",
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
)
FENCE_COUNTER_PATTERN = " ".join(
    rf"{name}=({UINT})" for name in FENCE_PERFORMANCE_COUNTERS
)
FENCE_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=fence "
    rf"mode=(compat|native|runtime_probe) seq=({UINT}) "
    rf"point=(E2E_START|CORE_START|ACK_SETTLED|E2E_END|PROBE_START|PROBE_END) "
    rf"tick_us=({UINT}) attempts=({UINT}) stable_rounds=({UINT}) "
    rf"observer_pid=({UINT}) observer_tick=({UINT}) "
    rf"observer_lifecycle_id=({UINT}) observer_lifecycle_generation=({UINT}) "
    rf"counter_scope=(global) {FENCE_COUNTER_PATTERN}$"
)
TRACE_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=trace seq=({UINT}) "
    rf"tick_us=({UINT}) role=(orchestrator|sentinel|investigator|recovery) "
    rf"event=(INCIDENT|DISCOVERED|HANDOFF|RECOVERY_COMMITTED|RECOVERED) "
    rf"value0=({UINT}) value1=({UINT})$"
)
RUNTIME_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=runtime mode=native "
    rf"agents=({UINT}) duration_us=({UINT}) tool_calls=({UINT}) "
    rf"dispatches=({UINT}) wait_sleeps=({UINT}) wait_wakeups=({UINT}) "
    rf"records_examined=({UINT}) denied_actions=({UINT}) "
    rf"duplicate_actions=({UINT}) recovery_side_effects=({UINT})$"
)
ORACLE_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=oracle "
    rf"project=([a-z0-9-]+) workflow=([a-z0-9-]+) run=([A-Z0-9-]+) "
    rf"stage=([a-z0-9-]+) reason=([a-z0-9_-]+) "
    rf"final_status=([a-z0-9-]+) "
    rf"execution_order=(compat_then_native|native_then_compat) "
    rf"corpus=({UINT}) outcome_hash=({UINT}) compat_hash=({UINT}) "
    rf"native_hash=({UINT})$"
)

MECHANISM_COUNTERS = (
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
)
MECHANISM_PAIR_PATTERN = " ".join(
    rf"before_{name}=({UINT}) after_{name}=({UINT})"
    for name in MECHANISM_COUNTERS
)
MECHANISM_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=mechanism "
    rf"mode=(compat|native|workflow|runtime_probe) "
    rf"scope=(core|end_to_end) observer_pid=({UINT}) "
    rf"before_tick=({UINT}) after_tick=({UINT}) "
    rf"observer_lifecycle_id=({UINT}) observer_lifecycle_generation=({UINT}) "
    rf"counter_scope=(global) {MECHANISM_PAIR_PATTERN}$"
)

_STORAGE_COUNTERS = FENCE_PERFORMANCE_COUNTERS
PATH_METRIC_FIELDS = (
    "core_duration_us",
    "end_to_end_duration_us",
    "workload_syscalls",
    "records_examined",
    "bytes_read",
    "directory_block_probes",
    "directory_entries_examined",
    "physical_reads",
    "physical_writes",
    "durable_flushes",
    "virtio_notifications",
    "virtio_submitted_requests",
    "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
)
ORDER_NAMES = {
    "compat_then_native": "traversal_then_indexed",
    "native_then_compat": "indexed_then_traversal",
}


def _load_guest_failure_classifier() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "guest_failure_classifier.py"
    spec = importlib.util.spec_from_file_location(
        "_agentos_contest_demo_guest_failure_classifier", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Guest failure classifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUEST_FAILURE_CLASSIFIER = _load_guest_failure_classifier()


class ContestDemoError(RuntimeError):
    """A Guest result is incomplete or internally inconsistent."""


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ContestDemoError(f"{label} must be a regular non-symlink file")
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _guest_lines(payload: bytes, label: str) -> list[str]:
    try:
        lines = payload.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise ContestDemoError(f"{label} is not strict UTF-8") from error
    for line in lines:
        failure = _GUEST_FAILURE_CLASSIFIER.classify_output_line(
            line, phase=_GUEST_FAILURE_CLASSIFIER.PHASE_GUEST
        )
        if failure is not None:
            raise ContestDemoError(f"{label} contains Guest failure: {failure}")
    return lines


def _read_guest(path: Path, label: str) -> list[str]:
    return _guest_lines(_regular_file(path, label).read_bytes(), label)


def _uint(raw: str, label: str) -> int:
    value = int(raw, 10)
    if value > UINT64_MAX:
        raise ContestDemoError(f"{label} exceeds uint64")
    return value


def _unique_match(
    lines: list[str], pattern: re.Pattern[str], label: str
) -> tuple[int, re.Match[str]]:
    found = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := pattern.fullmatch(line)) is not None
    ]
    if len(found) != 1:
        raise ContestDemoError(f"{label} must occur exactly once")
    return found[0]


def _fnv64(text: str) -> int:
    value = 1469598103934665603
    for byte in text.encode("ascii"):
        value ^= byte
        value = (value * 1099511628211) & UINT64_MAX
    return value


def _expected_outcome_hash() -> int:
    return _fnv64(
        "agentos-showcase-v2|lab-gene-x|nightly-regression|"
        "RUN-042|align|memory_limit|recovered"
    )


def _parse_event(match: re.Match[str]) -> dict[str, Any]:
    return {
        "nonce": _uint(match.group(1), "event nonce"),
        "mode": match.group(2),
        "sequence": _uint(match.group(3), "event sequence"),
        "tick_us": _uint(match.group(4), "event tick"),
        "role": match.group(5),
        "event": match.group(6),
        "value0": _uint(match.group(7), "event value0"),
        "value1": _uint(match.group(8), "event value1"),
    }


def _parse_run(match: re.Match[str]) -> dict[str, Any]:
    return {
        "nonce": _uint(match.group(1), "run nonce"),
        "id": _uint(match.group(2), "sample id"),
        "order": match.group(3),
    }


def _parse_metric(match: re.Match[str]) -> dict[str, Any]:
    return {
        "nonce": _uint(match.group(1), "metric nonce"),
        "mode": match.group(2),
        "actor_pid": _uint(match.group(3), "metric actor pid"),
        "core_duration_us": _uint(match.group(4), "metric core duration"),
        "end_to_end_duration_us": _uint(match.group(5), "metric e2e duration"),
        "end_to_end_started_us": _uint(match.group(6), "metric e2e start"),
        "end_to_end_finished_us": _uint(match.group(7), "metric e2e finish"),
        "workload_syscalls": _uint(match.group(8), "metric workload syscalls"),
        "records_examined": _uint(match.group(9), "metric records"),
        "bytes_read": _uint(match.group(10), "metric bytes"),
        "result_items": _uint(match.group(11), "metric results"),
        "outcome_hash": _uint(match.group(12), "metric outcome hash"),
    }


def _parse_fence(match: re.Match[str]) -> dict[str, Any]:
    result = {
        "nonce": _uint(match.group(1), "fence nonce"),
        "mode": match.group(2),
        "sequence": _uint(match.group(3), "fence sequence"),
        "point": match.group(4),
        "tick_us": _uint(match.group(5), "fence tick"),
        "attempts": _uint(match.group(6), "fence attempts"),
        "stable_rounds": _uint(match.group(7), "fence stable rounds"),
        "observer_pid": _uint(match.group(8), "fence observer pid"),
        "observer_tick": _uint(match.group(9), "fence observer tick"),
        "observer_lifecycle_id": _uint(match.group(10), "fence lifecycle id"),
        "observer_lifecycle_generation": _uint(
            match.group(11), "fence lifecycle generation"
        ),
        "counter_scope": match.group(12),
    }
    for offset, name in enumerate(FENCE_PERFORMANCE_COUNTERS, 13):
        result[name] = _uint(match.group(offset), f"fence {name}")
    return result


def _parse_trace(match: re.Match[str]) -> dict[str, Any]:
    return {
        "nonce": _uint(match.group(1), "trace nonce"),
        "sequence": _uint(match.group(2), "trace sequence"),
        "tick_us": _uint(match.group(3), "trace tick"),
        "role": match.group(4),
        "event": match.group(5),
        "value0": _uint(match.group(6), "trace value0"),
        "value1": _uint(match.group(7), "trace value1"),
    }


def _parse_runtime(match: re.Match[str]) -> dict[str, Any]:
    names = (
        "nonce",
        "agents",
        "duration_us",
        "tool_calls",
        "dispatches",
        "wait_sleeps",
        "wait_wakeups",
        "records_examined",
        "denied_actions",
        "duplicate_actions",
        "recovery_side_effects",
    )
    return {
        name: _uint(match.group(index), f"runtime {name}")
        for index, name in enumerate(names, 1)
    }


def _parse_oracle(match: re.Match[str]) -> dict[str, Any]:
    return {
        "nonce": _uint(match.group(1), "oracle nonce"),
        "project": match.group(2),
        "workflow": match.group(3),
        "run": match.group(4),
        "stage": match.group(5),
        "reason": match.group(6),
        "final_status": match.group(7),
        "execution_order": match.group(8),
        "corpus": _uint(match.group(9), "oracle corpus"),
        "outcome_hash": _uint(match.group(10), "oracle outcome"),
        "compat_hash": _uint(match.group(11), "oracle compat outcome"),
        "native_hash": _uint(match.group(12), "oracle native outcome"),
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _mechanism_derived(item: dict[str, Any]) -> dict[str, float | None]:
    batched = (
        item["virtio_batched_write_requests"]
        + item["virtio_batched_read_requests"]
    )
    return {
        "buffers_per_epoch": _ratio(
            item["epoch_buffers_staged"], item["epoch_commits"]
        ),
        "directory_entries_per_probe": _ratio(
            item["directory_entries_examined"], item["directory_block_probes"]
        ),
        "virtio_requests_per_notification": _ratio(
            item["virtio_submitted_requests"], item["virtio_notifications"]
        ),
        "virtio_write_requests_per_batch": _ratio(
            item["virtio_batched_write_requests"], item["virtio_write_batch_calls"]
        ),
        "virtio_read_requests_per_batch": _ratio(
            item["virtio_batched_read_requests"], item["virtio_read_batch_calls"]
        ),
        "virtio_batched_request_share_pct": None
        if item["virtio_submitted_requests"] == 0
        else round(100.0 * batched / item["virtio_submitted_requests"], 3),
    }


def _parse_mechanism(match: re.Match[str]) -> dict[str, Any]:
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    for offset, name in enumerate(MECHANISM_COUNTERS):
        before[name] = _uint(
            match.group(10 + offset * 2), f"mechanism before {name}"
        )
        after[name] = _uint(
            match.group(11 + offset * 2), f"mechanism after {name}"
        )
        if after[name] < before[name]:
            raise ContestDemoError(f"mechanism {name} is not monotonic")
    before_tick = _uint(match.group(5), "mechanism before tick")
    after_tick = _uint(match.group(6), "mechanism after tick")
    if after_tick <= before_tick:
        raise ContestDemoError("mechanism sample ticks are not increasing")
    result: dict[str, Any] = {
        "nonce": _uint(match.group(1), "mechanism nonce"),
        "mode": match.group(2),
        "scope": match.group(3),
        "observer_pid": _uint(match.group(4), "mechanism observer pid"),
        "before_tick": before_tick,
        "after_tick": after_tick,
        "observer_lifecycle_id": _uint(match.group(7), "mechanism lifecycle id"),
        "observer_lifecycle_generation": _uint(
            match.group(8), "mechanism lifecycle generation"
        ),
        "counter_scope": match.group(9),
        "raw_pair": {"before": before, "after": after},
    }
    result.update({name: after[name] - before[name] for name in MECHANISM_COUNTERS})
    result["raw_cycle_order_gap"] = after_tick - before_tick
    if result["virtio_notifications"] > result["virtio_submitted_requests"]:
        raise ContestDemoError("VirtIO notifications exceed submitted requests")
    if (
        result["virtio_write_batch_calls"] + result["virtio_read_batch_calls"]
        > result["virtio_notifications"]
        or result["virtio_batched_write_requests"]
        < result["virtio_write_batch_calls"]
        or result["virtio_batched_read_requests"]
        < result["virtio_read_batch_calls"]
        or result["virtio_batched_write_requests"]
        + result["virtio_batched_read_requests"]
        > result["virtio_submitted_requests"]
        or result["virtio_indirect_write_batch_calls"]
        > result["virtio_write_batch_calls"]
    ):
        raise ContestDemoError("VirtIO batch counters are inconsistent")
    if (
        result["directory_block_probes"] == 0
        and result["directory_entries_examined"] != 0
    ):
        raise ContestDemoError("directory entries lack a block probe")
    result.update(_mechanism_derived(result))
    return result


def _verify_fence_stream(
    mode: str,
    rows: list[dict[str, Any]],
    points: tuple[str, ...],
) -> None:
    if len(rows) != len(points):
        raise ContestDemoError(f"{mode} quiescence fences are incomplete")
    previous: dict[str, Any] | None = None
    for sequence, (row, point) in enumerate(zip(rows, points), 1):
        if row["sequence"] != sequence or row["point"] != point:
            raise ContestDemoError(f"{mode} quiescence fences are out of contract")
        if (
            row["attempts"] < 3
            or row["attempts"] > 16
            or row["stable_rounds"] != 2
            or row["counter_scope"] != "global"
        ):
            raise ContestDemoError(f"{mode} quiescence fence did not converge")
        if row["observer_pid"] == 0:
            raise ContestDemoError(f"{mode} fence is not fully settled")
        lifecycle = (
            row["observer_lifecycle_id"],
            row["observer_lifecycle_generation"],
        )
        if lifecycle[0] == 0 or lifecycle[1] == 0:
            raise ContestDemoError(f"{mode} fence observer kind is invalid")
        if previous is not None and (
            row["tick_us"] < previous["tick_us"]
            or row["observer_tick"] <= previous["observer_tick"]
            or row["observer_pid"] != previous["observer_pid"]
            or row["observer_lifecycle_id"] != previous["observer_lifecycle_id"]
            or row["observer_lifecycle_generation"]
            != previous["observer_lifecycle_generation"]
            or any(row[name] < previous[name] for name in _STORAGE_COUNTERS)
        ):
            raise ContestDemoError(f"{mode} fence counters are not monotonic")
        previous = row


def _check_mechanism_interval(
    mechanism: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    label: str,
) -> None:
    if (
        mechanism["counter_scope"] != "global"
        or mechanism["observer_pid"] != before["observer_pid"]
        or mechanism["observer_pid"] != after["observer_pid"]
        or mechanism["observer_lifecycle_id"] != before["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_id"] != after["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_generation"]
        != before["observer_lifecycle_generation"]
        or mechanism["observer_lifecycle_generation"]
        != after["observer_lifecycle_generation"]
        or mechanism["before_tick"] != before["observer_tick"]
        or mechanism["after_tick"] != after["observer_tick"]
    ):
        raise ContestDemoError(f"{label} mechanism observer does not match its fences")
    for name in _STORAGE_COUNTERS:
        if (
            mechanism["raw_pair"]["before"][name] != before[name]
            or mechanism["raw_pair"]["after"][name] != after[name]
            or mechanism[name] != after[name] - before[name]
        ):
            raise ContestDemoError(f"{label} mechanism does not match its fences")


def _check_core_mechanism(
    mechanism: dict[str, Any],
    core_start: dict[str, Any],
    settled: dict[str, Any],
    label: str,
) -> None:
    if (
        mechanism["counter_scope"] != "global"
        or mechanism["observer_pid"] != core_start["observer_pid"]
        or mechanism["observer_pid"] != settled["observer_pid"]
        or mechanism["observer_lifecycle_id"]
        != core_start["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_id"] != settled["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_generation"]
        != core_start["observer_lifecycle_generation"]
        or mechanism["observer_lifecycle_generation"]
        != settled["observer_lifecycle_generation"]
        or mechanism["before_tick"] != core_start["observer_tick"]
        or mechanism["after_tick"] > settled["observer_tick"]
    ):
        raise ContestDemoError(f"{label} mechanism observer is outside core interval")
    for name in _STORAGE_COUNTERS:
        if (
            mechanism["raw_pair"]["before"][name] != core_start[name]
            or mechanism["raw_pair"]["after"][name] > settled[name]
        ):
            raise ContestDemoError(f"{label} mechanism is outside core interval")


def verify_showcase(
    path: Path,
    expected_nonce: int | None = None,
    expected_sample: int | None = None,
    expected_order: str | None = None,
    *,
    guest_bytes: bytes | None = None,
) -> dict[str, Any]:
    lines = (
        _read_guest(path, "showcase Guest log")
        if guest_bytes is None
        else _guest_lines(guest_bytes, "showcase Guest log snapshot")
    )
    legacy = {
        key: _unique_match(lines, pattern, f"legacy {key}")
        for key, pattern in LEGACY_PATTERNS.items()
    }
    positions = [legacy[key][0] for key in LEGACY_PATTERNS]
    if positions != sorted(positions):
        raise ContestDemoError("legacy workflow markers are out of order")

    events: dict[str, list[dict[str, Any]]] = {"compat": [], "native": []}
    metrics: dict[str, dict[str, Any]] = {}
    fences: dict[str, list[dict[str, Any]]] = {
        "compat": [],
        "native": [],
        "runtime_probe": [],
    }
    trace: list[dict[str, Any]] = []
    runtime: dict[str, Any] | None = None
    oracle: dict[str, Any] | None = None
    run_record: dict[str, Any] | None = None
    mechanisms: dict[tuple[str, str], dict[str, Any]] = {}
    for line in lines:
        if not line.startswith("agentos:demo "):
            continue
        if match := EVENT_PATTERN.fullmatch(line):
            event = _parse_event(match)
            events[event["mode"]].append(event)
        elif match := RUN_PATTERN.fullmatch(line):
            if run_record is not None:
                raise ContestDemoError("showcase run record is duplicated")
            run_record = _parse_run(match)
        elif match := METRIC_PATTERN.fullmatch(line):
            metric = _parse_metric(match)
            if metric["mode"] in metrics:
                raise ContestDemoError("showcase metric is duplicated")
            metrics[metric["mode"]] = metric
        elif match := FENCE_PATTERN.fullmatch(line):
            fence = _parse_fence(match)
            fences[fence["mode"]].append(fence)
        elif match := TRACE_PATTERN.fullmatch(line):
            trace.append(_parse_trace(match))
        elif match := RUNTIME_PATTERN.fullmatch(line):
            if runtime is not None:
                raise ContestDemoError("native runtime record is duplicated")
            runtime = _parse_runtime(match)
        elif match := ORACLE_PATTERN.fullmatch(line):
            if oracle is not None:
                raise ContestDemoError("showcase oracle is duplicated")
            oracle = _parse_oracle(match)
        elif match := MECHANISM_PATTERN.fullmatch(line):
            mechanism = _parse_mechanism(match)
            key = (mechanism["mode"], mechanism["scope"])
            if key in mechanisms:
                raise ContestDemoError("showcase mechanism record is duplicated")
            mechanisms[key] = mechanism
        else:
            raise ContestDemoError("unknown or non-canonical schema=2 showcase record")

    if (
        set(metrics) != {"compat", "native"}
        or runtime is None
        or oracle is None
        or run_record is None
    ):
        raise ContestDemoError("showcase metrics, runtime, or oracle are incomplete")
    if run_record["id"] == 0:
        raise ContestDemoError("sample id must be positive")
    if expected_sample is not None and run_record["id"] != expected_sample:
        raise ContestDemoError("showcase sample id differs from the campaign slot")
    if expected_order is not None and run_record["order"] != expected_order:
        raise ContestDemoError("showcase order differs from the campaign slot")

    nonce_values = {
        run_record["nonce"],
        *(item["nonce"] for rows in events.values() for item in rows),
        *(item["nonce"] for item in metrics.values()),
        *(item["nonce"] for rows in fences.values() for item in rows),
        *(item["nonce"] for item in trace),
        *(item["nonce"] for item in mechanisms.values()),
        runtime["nonce"],
        oracle["nonce"],
    }
    if len(nonce_values) != 1 or 0 in nonce_values:
        raise ContestDemoError("showcase records do not share one nonzero Guest nonce")
    guest_nonce = next(iter(nonce_values))
    if expected_nonce is not None and guest_nonce != expected_nonce:
        raise ContestDemoError("showcase Guest nonce differs from the campaign")

    expected_events = (
        (1, "orchestrator", "INCIDENT"),
        (2, None, "DISCOVERED"),
        (3, "recovery", "RECOVERY_COMMITTED"),
        (4, "orchestrator", "RECOVERED"),
    )
    for mode, rows in events.items():
        if len(rows) != len(expected_events):
            raise ContestDemoError(f"{mode} event stream is incomplete")
        for row, (sequence, role, event_name) in zip(rows, expected_events):
            if (
                row["sequence"] != sequence
                or (role is not None and row["role"] != role)
                or row["event"] != event_name
            ):
                raise ContestDemoError(f"{mode} event stream is out of contract")
        ticks = [row["tick_us"] for row in rows]
        if ticks != sorted(ticks):
            raise ContestDemoError(f"{mode} event ticks are not monotonic")
        metric = metrics[mode]
        if metric["core_duration_us"] != ticks[-1] - ticks[0]:
            raise ContestDemoError(f"{mode} core duration does not match timestamps")
        if (
            metric["end_to_end_duration_us"]
            != metric["end_to_end_finished_us"] - metric["end_to_end_started_us"]
            or metric["end_to_end_duration_us"] < metric["core_duration_us"]
        ):
            raise ContestDemoError(f"{mode} end-to-end timing is inconsistent")
        if (
            rows[1]["value0"] != metric["records_examined"]
            or rows[1]["value1"] != metric["bytes_read"]
            or rows[2]["value0"] != metric["workload_syscalls"]
            or rows[2]["value1"] != 1
            or rows[3]["value0"] != 1
            or metric["result_items"] != 1
        ):
            raise ContestDemoError(f"{mode} events do not bind their metric")
        lane_fences = fences[mode]
        _verify_fence_stream(
            mode,
            lane_fences,
            ("E2E_START", "CORE_START", "ACK_SETTLED", "E2E_END"),
        )
        if not (
            lane_fences[0]["tick_us"]
            <= metric["end_to_end_started_us"]
            <= lane_fences[1]["tick_us"]
            <= ticks[0]
            <= ticks[-1]
            <= lane_fences[2]["tick_us"]
            <= lane_fences[3]["tick_us"]
            <= metric["end_to_end_finished_us"]
        ):
            raise ContestDemoError(f"{mode} timing is not bracketed by its fences")

    compat = metrics["compat"]
    native = metrics["native"]
    if compat["actor_pid"] == 0 or compat["actor_pid"] != native["actor_pid"]:
        raise ContestDemoError("compat/native lanes did not run in one actor")
    if (
        compat["workload_syscalls"] == 0
        or compat["records_examined"] != CORPUS_SIZE + 1
        or compat["bytes_read"] == 0
    ):
        raise ContestDemoError("compat lane did not traverse the complete corpus")
    if (
        native["workload_syscalls"] == 0
        or native["records_examined"] == 0
        or native["records_examined"] >= compat["records_examined"]
        or native["bytes_read"] != 0
    ):
        raise ContestDemoError("native lane did not use the indexed control path")

    expected_hash = _expected_outcome_hash()
    expected_oracle = {
        "project": "lab-gene-x",
        "workflow": "nightly-regression",
        "run": "RUN-042",
        "stage": "align",
        "reason": "memory_limit",
        "final_status": "recovered",
        "execution_order": run_record["order"],
        "corpus": CORPUS_SIZE,
    }
    if any(oracle[key] != value for key, value in expected_oracle.items()):
        raise ContestDemoError("showcase workload differs from the expected query")
    if {
        compat["outcome_hash"],
        native["outcome_hash"],
        oracle["outcome_hash"],
        oracle["compat_hash"],
        oracle["native_hash"],
    } != {expected_hash}:
        raise ContestDemoError("traversal and indexed query outcomes differ")

    expected_trace = (
        (1, "orchestrator", "INCIDENT"),
        (2, "sentinel", "DISCOVERED"),
        (3, "investigator", "HANDOFF"),
        (4, "recovery", "RECOVERY_COMMITTED"),
        (5, "orchestrator", "RECOVERED"),
    )
    if len(trace) != len(expected_trace):
        raise ContestDemoError("multi-Agent workflow trace is incomplete")
    for row, expected in zip(trace, expected_trace):
        if (row["sequence"], row["role"], row["event"]) != expected:
            raise ContestDemoError("multi-Agent workflow trace is out of contract")
    trace_ticks = [row["tick_us"] for row in trace]
    if trace_ticks != sorted(trace_ticks):
        raise ContestDemoError("multi-Agent workflow ticks are not monotonic")
    if runtime["duration_us"] != trace_ticks[-1] - trace_ticks[0]:
        raise ContestDemoError("runtime duration does not match the workflow")
    if (
        runtime["agents"] != 3
        or runtime["tool_calls"] == 0
        or runtime["records_examined"] == 0
        or runtime["wait_sleeps"] < 3
        or runtime["wait_wakeups"] < 3
        or runtime["denied_actions"] != 1
        or runtime["duplicate_actions"] != 1
        or runtime["recovery_side_effects"] != 1
        or trace[1]["value0"] == 0
        or trace[3]["value0"] != 1
        or trace[3]["value1"] != 1
    ):
        raise ContestDemoError("multi-Agent runtime counters violate the workflow")

    expected_mechanisms = {
        ("compat", "core"),
        ("compat", "end_to_end"),
        ("native", "core"),
        ("native", "end_to_end"),
        ("workflow", "end_to_end"),
        ("runtime_probe", "end_to_end"),
    }
    if set(mechanisms) != expected_mechanisms:
        raise ContestDemoError("showcase mechanism records are incomplete")
    _verify_fence_stream(
        "runtime_probe",
        fences["runtime_probe"],
        ("PROBE_START", "PROBE_END"),
    )
    for mode in ("compat", "native"):
        _check_core_mechanism(
            mechanisms[(mode, "core")],
            fences[mode][1],
            fences[mode][2],
            f"{mode} core",
        )
        if (
            metrics[mode]["workload_syscalls"]
            != mechanisms[(mode, "core")]["workload_syscalls"]
        ):
            raise ContestDemoError(f"{mode} syscall metric differs from its snapshot")
        _check_mechanism_interval(
            mechanisms[(mode, "end_to_end")],
            fences[mode][0],
            fences[mode][3],
            f"{mode} end-to-end",
        )
    _check_mechanism_interval(
        mechanisms[("runtime_probe", "end_to_end")],
        fences["runtime_probe"][0],
        fences["runtime_probe"][1],
        "runtime probe",
    )

    lane_observer = (
        fences["compat"][0]["observer_pid"],
        fences["compat"][0]["observer_lifecycle_id"],
        fences["compat"][0]["observer_lifecycle_generation"],
    )
    if lane_observer[0] != compat["actor_pid"]:
        raise ContestDemoError("performance observer is not the workload actor")
    for mode in ("compat", "native"):
        if any(
            (
                row["observer_pid"],
                row["observer_lifecycle_id"],
                row["observer_lifecycle_generation"],
            )
            != lane_observer
            for row in fences[mode]
        ):
            raise ContestDemoError("query lanes changed their performance observer")
    workflow = mechanisms[("workflow", "end_to_end")]
    if (
        (
            workflow["observer_pid"],
            workflow["observer_lifecycle_id"],
            workflow["observer_lifecycle_generation"],
        )
        != lane_observer
        or workflow["before_tick"]
        <= max(
            fences["compat"][-1]["observer_tick"],
            fences["native"][-1]["observer_tick"],
        )
    ):
        raise ContestDemoError("workflow snapshot changed the performance observer")
    probe = mechanisms[("runtime_probe", "end_to_end")]
    if (
        probe["observer_pid"] == compat["actor_pid"]
        or probe["observer_lifecycle_id"] == 0
        or probe["observer_lifecycle_generation"] == 0
    ):
        raise ContestDemoError("runtime probe is not bound to the bootstrap observer")
    if any(
        probe[name] == 0
        for name in (
            "cow_shared_pages",
            "cow_copied_pages",
            "cow_fault_promotions",
            "exec_cache_hits",
            "exec_cache_misses",
            "exec_cache_shared_pages",
        )
    ):
        raise ContestDemoError("runtime probe did not exercise COW and exec reuse")

    clean_metrics = {
        mode: {
            key: value
            for key, value in metric.items()
            if key not in {"nonce", "mode"}
        }
        for mode, metric in metrics.items()
    }
    nested_mechanisms: dict[str, dict[str, dict[str, Any]]] = {}
    for (mode, scope), mechanism in mechanisms.items():
        clean = {
            key: value
            for key, value in mechanism.items()
            if key not in {"nonce", "mode", "scope"}
        }
        nested_mechanisms.setdefault(mode, {})[scope] = clean
    clean_runtime = {key: value for key, value in runtime.items() if key != "nonce"}
    return {
        "guest_nonce": guest_nonce,
        "sample": {
            "id": run_record["id"],
            "order": ORDER_NAMES[run_record["order"]],
        },
        "lanes": {
            "traversal": clean_metrics["compat"],
            "indexed": clean_metrics["native"],
        },
        "mechanisms": {
            "traversal": nested_mechanisms["compat"],
            "indexed": nested_mechanisms["native"],
            "workflow": nested_mechanisms["workflow"],
            "runtime_probe": nested_mechanisms["runtime_probe"],
        },
        "workflow": clean_runtime,
        "outcome": {
            "final_status": oracle["final_status"],
            "outcome_hash": oracle["outcome_hash"],
            "equal": True,
        },
    }


def _median(values: list[int | float]) -> int | float:
    value = statistics.median(values)
    if float(value).is_integer():
        return int(value)
    return round(float(value), 3)


def _path_measurement(sample: dict[str, Any], path_name: str) -> dict[str, Any]:
    lane = sample["lanes"][path_name]
    mechanism = sample["mechanisms"][path_name]["end_to_end"]
    result = {
        name: lane[name]
        for name in (
            "core_duration_us",
            "end_to_end_duration_us",
            "workload_syscalls",
            "records_examined",
            "bytes_read",
        )
    }
    result.update(
        {
            name: mechanism[name]
            for name in PATH_METRIC_FIELDS
            if name not in result
        }
    )
    return result


def campaign_aggregates(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if len(samples) < 2 or len(samples) % 2 != 0:
        raise ContestDemoError("campaign requires an even set of at least two samples")
    order_counts = {
        order: sum(sample["sample"]["order"] == order for sample in samples)
        for order in ("traversal_then_indexed", "indexed_then_traversal")
    }
    if len(set(order_counts.values())) != 1:
        raise ContestDemoError("campaign is not AB/BA balanced")

    outcome = samples[0]["outcome"]
    if any(sample["outcome"] != outcome for sample in samples[1:]):
        raise ContestDemoError("campaign outcomes differ between QEMU boots")

    rows: list[dict[str, Any]] = []
    for sample in samples:
        traversal = _path_measurement(sample, "traversal")
        indexed = _path_measurement(sample, "indexed")
        rows.append(
            {
                "sample_id": sample["sample"]["id"],
                "order": sample["sample"]["order"],
                "traversal": traversal,
                "indexed": indexed,
            }
        )
    medians = {
        path_name: {
            field: _median([row[path_name][field] for row in rows])
            for field in PATH_METRIC_FIELDS
        }
        for path_name in ("traversal", "indexed")
    }
    core_deltas = [
        row["indexed"]["core_duration_us"]
        - row["traversal"]["core_duration_us"]
        for row in rows
    ]
    indexed_wins = sum(delta < 0 for delta in core_deltas)
    paired_median_delta = _median(core_deltas)
    if indexed_wins <= len(rows) // 2 or paired_median_delta >= 0:
        raise ContestDemoError(
            "indexed query did not beat traversal in a majority of paired boots"
        )
    if any(
        row["indexed"]["records_examined"]
        >= row["traversal"]["records_examined"]
        for row in rows
    ):
        raise ContestDemoError("indexed query did not reduce examined records")
    return {
        "comparison": {
            "workload": "same_kernel_same_guest_same_file_query",
            "paths": {
                "traversal": "directory_traversal",
                "indexed": "indexed_control_path",
            },
            "corpus_records": CORPUS_SIZE,
            "order_balance": order_counts,
            "medians": medians,
            "ratios": {
                "traversal_over_indexed_core_duration": _ratio(
                    medians["traversal"]["core_duration_us"],
                    medians["indexed"]["core_duration_us"],
                ),
                "traversal_over_indexed_end_to_end_duration": _ratio(
                    medians["traversal"]["end_to_end_duration_us"],
                    medians["indexed"]["end_to_end_duration_us"],
                ),
                "traversal_over_indexed_records_examined": _ratio(
                    medians["traversal"]["records_examined"],
                    medians["indexed"]["records_examined"],
                ),
            },
            "paired_regression": {
                "sample_count": len(rows),
                "indexed_faster_samples": indexed_wins,
                "indexed_faster_majority": True,
                "median_indexed_minus_traversal_core_us": paired_median_delta,
                "indexed_reduced_records_in_all_samples": True,
            },
        },
        "samples": rows,
        "workflow": {
            name: _median([sample["workflow"][name] for sample in samples])
            for name in (
                "agents",
                "tool_calls",
                "dispatches",
                "wait_sleeps",
                "wait_wakeups",
                "denied_actions",
                "duplicate_actions",
                "recovery_side_effects",
            )
        },
        "outcome": outcome,
    }


def build_report(lab_logs: list[Path], elapsed_seconds: float) -> dict[str, Any]:
    if elapsed_seconds <= 0:
        raise ContestDemoError("elapsed time must be positive")
    if len(lab_logs) < 2 or len(lab_logs) % 2 != 0:
        raise ContestDemoError("campaign requires an even set of at least two QEMU logs")

    checked_logs = [
        _regular_file(path, f"showcase Guest log {index}")
        for index, path in enumerate(lab_logs, 1)
    ]
    samples: list[dict[str, Any]] = []
    campaign_nonce: int | None = None
    for index, log in enumerate(checked_logs, 1):
        expected_order = "compat_then_native" if index % 2 else "native_then_compat"
        sample = verify_showcase(log, campaign_nonce, index, expected_order)
        if campaign_nonce is None:
            campaign_nonce = sample["guest_nonce"]
        sample.pop("guest_nonce")
        samples.append(sample)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign": {
            "qemu_boots": len(samples),
            "wall_seconds": round(elapsed_seconds, 3),
            "ordering": "balanced_AB_BA",
            "qemu_jobs": 1,
        },
        **campaign_aggregates(samples),
    }


def render_csv(report: dict[str, Any]) -> str:
    fields = ["sample_id", "order"]
    for path_name in ("traversal", "indexed"):
        fields.extend(f"{path_name}_{name}" for name in PATH_METRIC_FIELDS)
    fields.extend(
        (
            "indexed_minus_traversal_core_us",
            "traversal_over_indexed_core_ratio",
        )
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for sample in report["samples"]:
        row: dict[str, Any] = {
            "sample_id": sample["sample_id"],
            "order": sample["order"],
        }
        for path_name in ("traversal", "indexed"):
            for name in PATH_METRIC_FIELDS:
                row[f"{path_name}_{name}"] = sample[path_name][name]
        row["indexed_minus_traversal_core_us"] = (
            sample["indexed"]["core_duration_us"]
            - sample["traversal"]["core_duration_us"]
        )
        row["traversal_over_indexed_core_ratio"] = _ratio(
            sample["traversal"]["core_duration_us"],
            sample["indexed"]["core_duration_us"],
        )
        writer.writerow(row)
    return output.getvalue()


def render_markdown(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    medians = comparison["medians"]
    traversal = medians["traversal"]
    indexed = medians["indexed"]
    lines = [
        "# AgentOS file-query measurements",
        "",
        f"{report['campaign']['qemu_boots']} real QEMU boots, balanced AB/BA order. "
        "Each boot runs a directory traversal and the indexed control path against "
        f"the same {comparison['corpus_records']}-record corpus.",
        "",
        "## Median comparison",
        "",
        "| Path | Core (us) | End-to-end (us) | Records examined | Bytes read | "
        "Directory probes | Directory entries |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| traversal | {} | {} | {} | {} | {} | {} |".format(
            traversal["core_duration_us"],
            traversal["end_to_end_duration_us"],
            traversal["records_examined"],
            traversal["bytes_read"],
            traversal["directory_block_probes"],
            traversal["directory_entries_examined"],
        ),
        "| indexed | {} | {} | {} | {} | {} | {} |".format(
            indexed["core_duration_us"],
            indexed["end_to_end_duration_us"],
            indexed["records_examined"],
            indexed["bytes_read"],
            indexed["directory_block_probes"],
            indexed["directory_entries_examined"],
        ),
        "",
        "Traversal/indexed core ratio: `{}`. Traversal/indexed records ratio: `{}`.".format(
            comparison["ratios"]["traversal_over_indexed_core_duration"],
            comparison["ratios"]["traversal_over_indexed_records_examined"],
        ),
        "Indexed was faster in `{}/{}` paired boots; the paired median "
        "indexed-minus-traversal core delta was `{} us`.".format(
            comparison["paired_regression"]["indexed_faster_samples"],
            comparison["paired_regression"]["sample_count"],
            comparison["paired_regression"][
                "median_indexed_minus_traversal_core_us"
            ],
        ),
        "",
        "## Raw paired measurements",
        "",
        "| Boot | Order | Traversal core (us) | Indexed core (us) | "
        "Traversal records | Indexed records | Traversal bytes | Indexed bytes |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sample in report["samples"]:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                sample["sample_id"],
                sample["order"],
                sample["traversal"]["core_duration_us"],
                sample["indexed"]["core_duration_us"],
                sample["traversal"]["records_examined"],
                sample["indexed"]["records_examined"],
                sample["traversal"]["bytes_read"],
                sample["indexed"]["bytes_read"],
            )
        )
    lines.extend(
        (
            "",
            "Both paths produced the same verified outcome: "
            f"`{report['outcome']['final_status']}` "
            f"(hash `{report['outcome']['outcome_hash']}`).",
            "",
            "The original QEMU logs remain beside this report; measurements.csv "
            "contains the per-boot numeric rows.",
        )
    )
    return "\n".join(lines) + "\n"


def publish(report: dict[str, Any], output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise ContestDemoError("output directory must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(output_dir / "summary.json", serialized.encode("utf-8"))
    _atomic_write(output_dir / "measurements.csv", render_csv(report).encode("utf-8"))
    _atomic_write(output_dir / "report.md", render_markdown(report).encode("utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-log", action="append", type=Path, required=True)
    parser.add_argument("--elapsed-seconds", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(args.lab_log, args.elapsed_seconds)
        publish(report, args.output_dir)
    except (ContestDemoError, OSError, UnicodeError, ValueError) as error:
        raise SystemExit(f"contest demo failed: {error}") from error
    medians = report["comparison"]["medians"]
    ratios = report["comparison"]["ratios"]
    print(
        "[contest-demo] traversal_core={}us indexed_core={}us ratio={}".format(
            medians["traversal"]["core_duration_us"],
            medians["indexed"]["core_duration_us"],
            ratios["traversal_over_indexed_core_duration"],
        )
    )
    print(
        "[contest-demo] records traversal={} indexed={} outcome_hash={}".format(
            medians["traversal"]["records_examined"],
            medians["indexed"]["records_examined"],
            report["outcome"]["outcome_hash"],
        )
    )
    print(f"[contest-demo] results: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
