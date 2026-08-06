#!/usr/bin/env python3
"""Verify one live AgentOS Guest and render its measured showcase."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from committed_source_identity import committed_source_path_sample
from evidence_delivery_contract import (
    DeliveryContractError,
    SAFE_GIT_CONFIG_ARGUMENTS,
    controlled_git_environment,
    tracked_worktree_identity,
)


SCHEMA_VERSION = 6
KIND = "agentos-contest-showcase"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9a-f]{16}$")
UINT = r"(?:0|[1-9][0-9]*)"
UINT64_MAX = (1 << 64) - 1
CORPUS_SIZE = 24
DEMO_SOURCE_PATHS = (
    "agent_performance_abi.h",
    "user/src/labdemo_ucore.c",
    "user/src/labdemo_execprobe_ucore.c",
    "user/include/labdemo_workload.h",
    "user/include/agent.h",
    "user/include/exec_policy_manifest.h",
    "user/lib/syscall.c",
    "user/lib/syscall_ids.h",
    "agent_tool_abi.h",
    "agent_observe_abi.h",
    "os/agent_resource.c",
    "os/agent_identity.c",
    "os/performance_stats.c",
    "os/performance_stats.h",
    "os/bio.c",
    "os/bio.h",
    "os/fs.c",
    "os/fs.h",
    "os/fs_epoch.c",
    "os/fs_epoch.h",
    "os/virtio_disk.c",
    "os/virtio.h",
    "os/agent_metadata_store.c",
    "os/vm.c",
    "os/vm.h",
    "os/loader.c",
    "os/loader.h",
    "os/syscall.c",
    "os/syscall_ids.h",
    "scripts/run-contest-demo.sh",
    "host_tools/contest_demo.py",
)
LEGACY_PATTERNS = {
    "startup": re.compile(
        r"^labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3$"
    ),
    "audit": re.compile(
        r"^labdemo_ucore: global_audit=1 records=([1-9][0-9]*) "
        r"agents=3 context=1 event=1 sched=1 prefetch=1$"
    ),
    "timeline": re.compile(
        r"^labdemo_ucore: unified_timeline records=([1-9][0-9]*) "
        r"context=1 event=1 sched=1 prefetch=1$"
    ),
    "provenance": re.compile(
        r"^labdemo_ucore: provenance_graph edges=([1-9][0-9]*) "
        r"message=1 prefetch=1$"
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
    rf"end_to_end_finished_us=({UINT}) "
    rf"workload_syscalls=({UINT}) "
    rf"records_examined=({UINT}) bytes_read=({UINT}) "
    rf"result_items=({UINT}) outcome_hash=({UINT})$"
)
FENCE_PERFORMANCE_COUNTERS = (
    "epoch_commits", "epoch_buffers_staged", "physical_writes", "physical_reads",
    "durable_flushes", "deduplicated_stages", "workload_syscalls",
    "directory_block_probes", "directory_entries_examined",
    "virtio_notifications", "virtio_submitted_requests",
    "virtio_write_batch_calls", "virtio_batched_write_requests",
    "virtio_indirect_write_batch_calls",
    "virtio_read_batch_calls", "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
)
FENCE_METADATA_FIELDS = (
    "metadata_dirty", "metadata_durable", "metadata_requests",
    "metadata_coalesced", "metadata_commits", "metadata_pending",
)
FENCE_COUNTER_PATTERN = " ".join(
    rf"{name}=({UINT})"
    for name in FENCE_PERFORMANCE_COUNTERS + FENCE_METADATA_FIELDS
)
FENCE_PATTERN = re.compile(
    rf"^agentos:demo schema=2 nonce=({UINT}) kind=fence "
    rf"mode=(compat|native|runtime_probe) seq=({UINT}) "
    rf"point=(E2E_START|CORE_START|ACK_SETTLED|E2E_END|PROBE_START|PROBE_END) "
    rf"tick_us=({UINT}) attempts=({UINT}) stable_rounds=({UINT}) "
    rf"observer_pid=({UINT}) observer_tick=({UINT}) "
    rf"observer_lifecycle_id=({UINT}) "
    rf"observer_lifecycle_generation=({UINT}) "
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
    rf"corpus=({UINT}) "
    rf"outcome_hash=({UINT}) compat_hash=({UINT}) native_hash=({UINT})$"
)
MECHANISM_COUNTERS = (
    "epoch_commits", "epoch_buffers_staged", "physical_writes", "physical_reads",
    "durable_flushes", "deduplicated_stages", "cow_shared_pages",
    "cow_copied_pages", "cow_fault_promotions", "exec_cache_hits",
    "exec_cache_misses", "exec_cache_shared_pages", "exec_cache_evictions",
    "workload_syscalls", "directory_block_probes",
    "directory_entries_examined", "virtio_notifications",
    "virtio_submitted_requests", "virtio_write_batch_calls",
    "virtio_batched_write_requests", "virtio_indirect_write_batch_calls",
    "virtio_read_batch_calls", "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
    "metadata_dirty", "metadata_durable", "metadata_requests",
    "metadata_coalesced", "metadata_commits",
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
    rf"observer_lifecycle_id=({UINT}) "
    rf"observer_lifecycle_generation=({UINT}) counter_scope=(global) "
    rf"{MECHANISM_PAIR_PATTERN} "
    rf"before_metadata_pending=({UINT}) after_metadata_pending=({UINT})$"
)

LANE_EFFECT_SPECS = (
    ("core_duration_us", "恢复核心耗时", "us"),
    ("end_to_end_duration_us", "端到端耗时", "us"),
    ("workload_syscalls", "工作负载系统调用", "call"),
    ("records_examined", "检查记录", "record"),
    ("bytes_read", "读取字节", "byte"),
)
MECHANISM_EFFECT_SPECS = (
    ("workload_syscalls", "工作负载系统调用", "call"),
    ("directory_block_probes", "目录块探测", "block"),
    ("directory_entries_examined", "目录项检查", "entry"),
    ("epoch_commits", "FS epoch 提交", "commit"),
    ("epoch_buffers_staged", "FS epoch 暂存 buffer", "buffer"),
    ("physical_writes", "块设备物理写", "request"),
    ("physical_reads", "块设备物理读", "request"),
    ("durable_flushes", "持久化 flush", "flush"),
    ("deduplicated_stages", "Epoch 去重 stage", "stage"),
    ("virtio_notifications", "VirtIO 通知", "notify"),
    ("virtio_submitted_requests", "VirtIO 请求", "request"),
    ("virtio_write_batch_calls", "VirtIO 写批次", "batch"),
    ("virtio_batched_write_requests", "批量写请求", "request"),
    ("virtio_indirect_write_batch_calls", "VirtIO 间接写批次", "batch"),
    ("virtio_read_batch_calls", "VirtIO 读批次", "batch"),
    ("virtio_batched_read_requests", "批量读请求", "request"),
    ("overwrite_prereads_skipped", "覆盖写跳过预读", "block"),
    ("metadata_requests", "Metadata 请求", "request"),
    ("metadata_coalesced", "Metadata 合并请求", "request"),
    ("metadata_commits", "Metadata 提交", "commit"),
    ("buffers_per_epoch", "每 Epoch buffer", "buffer/commit"),
    ("directory_entries_per_probe", "每目录块检查项", "entry/block"),
    ("virtio_requests_per_notification", "每次通知请求", "request/notify"),
    ("virtio_write_requests_per_batch", "每写批次请求", "request/batch"),
    ("virtio_read_requests_per_batch", "每读批次请求", "request/batch"),
    ("virtio_batched_request_share_pct", "VirtIO 批量覆盖", "%"),
    ("metadata_coalescing_rate_pct", "Metadata 合并率", "%"),
    ("metadata_requests_per_commit", "每次 Metadata 提交请求", "request/commit"),
)

COUNTER_SCOPES = {
    "default": "global",
    "workload_syscalls": "observer_process",
}
REMOVED_PUBLISHED_FILES = ("dashboard-data.json", "timeline.json")


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
    """The live Guest record is incomplete, inconsistent, or untrusted."""


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *SAFE_GIT_CONFIG_ARGUMENTS, "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        env=controlled_git_environment(),
    )
    if result.returncode != 0:
        raise ContestDemoError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def clean_source_identity(root: Path) -> str:
    root = root.resolve()
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ContestDemoError("source root is not the Git worktree root")
    commit = _run_git(root, "rev-parse", "--verify", "HEAD")
    if not COMMIT.fullmatch(commit):
        raise ContestDemoError("HEAD is not a full commit identity")
    try:
        tracked_clean, _tracked_digest = tracked_worktree_identity("git", root)
    except DeliveryContractError as error:
        raise ContestDemoError(f"tracked source identity is unsafe: {error}") from error
    if not tracked_clean:
        raise ContestDemoError("tracked source bytes differ from HEAD; worktree is dirty")
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContestDemoError("source worktree is dirty; commit before running contest-demo")
    if _run_git(root, "rev-parse", "--verify", "HEAD") != commit:
        raise ContestDemoError("source commit changed during identity verification")
    return commit


def _measurement_source_sample(
    root: Path, commit: str, *, snapshot_root: Path | None = None
) -> tuple[tuple[str, int, str, str], ...]:
    try:
        return committed_source_path_sample(
            "git", root, commit, DEMO_SOURCE_PATHS, snapshot_root=snapshot_root
        )
    except DeliveryContractError as error:
        raise ContestDemoError(
            f"showcase source is not bound to commit: {error}"
        ) from error


def _source_receipt(sample: tuple[tuple[str, int, str, str], ...]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agentos-showcase-source-receipt",
        "sources": [
            {"path": path, "bytes": size, "sha256": sha256, "git_oid": oid}
            for path, size, sha256, oid in sample
        ],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    path = _regular_file(path, label)
    return _guest_lines(path.read_bytes(), label)


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
        "observer_lifecycle_id": _uint(
            match.group(10), "fence observer lifecycle id"
        ),
        "observer_lifecycle_generation": _uint(
            match.group(11), "fence observer lifecycle generation"
        ),
        "counter_scope": match.group(12),
    }
    for offset, name in enumerate(
        FENCE_PERFORMANCE_COUNTERS + FENCE_METADATA_FIELDS, 13
    ):
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
        "nonce", "agents", "duration_us", "tool_calls", "dispatches",
        "wait_sleeps", "wait_wakeups", "records_examined", "denied_actions",
        "duplicate_actions", "recovery_side_effects",
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
    result = {
        "nonce": _uint(match.group(1), "mechanism nonce"),
        "mode": match.group(2),
        "scope": match.group(3),
        "observer_pid": _uint(match.group(4), "mechanism observer pid"),
        "before_tick": before_tick,
        "after_tick": after_tick,
        "observer_lifecycle_id": _uint(
            match.group(7), "mechanism observer lifecycle id"
        ),
        "observer_lifecycle_generation": _uint(
            match.group(8), "mechanism observer lifecycle generation"
        ),
        "counter_scope": match.group(9),
        "before_metadata_pending": _uint(
            match.group(10 + len(MECHANISM_COUNTERS) * 2),
            "mechanism before metadata pending",
        ),
        "after_metadata_pending": _uint(
            match.group(11 + len(MECHANISM_COUNTERS) * 2),
            "mechanism after metadata pending",
        ),
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
    if result["metadata_coalesced"] > result["metadata_requests"]:
        raise ContestDemoError("metadata coalescing exceeds requests")
    if result["directory_block_probes"] == 0 and result[
        "directory_entries_examined"
    ] != 0:
        raise ContestDemoError("directory entries lack a block probe")
    result.update(_mechanism_derived(result))
    return result


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
        "metadata_coalescing_rate_pct": None
        if item["metadata_requests"] == 0
        else round(
            100.0 * item["metadata_coalesced"] / item["metadata_requests"], 3
        ),
        "metadata_requests_per_commit": _ratio(
            item["metadata_requests"], item["metadata_commits"]
        ),
    }


_STORAGE_COUNTERS = FENCE_PERFORMANCE_COUNTERS

_METADATA_COUNTERS = (
    "metadata_dirty",
    "metadata_durable",
    "metadata_requests",
    "metadata_coalesced",
    "metadata_commits",
)


def _verify_fence_stream(
    mode: str,
    rows: list[dict[str, Any]],
    points: tuple[str, ...],
    *,
    require_workflow_lifecycle: bool = True,
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
        if (
            row["observer_pid"] == 0
            or row["metadata_pending"] != 0
            or row["metadata_dirty"] != row["metadata_durable"]
        ):
            raise ContestDemoError(f"{mode} fence is not fully settled")
        lifecycle = (
            row["observer_lifecycle_id"],
            row["observer_lifecycle_generation"],
        )
        if (
            require_workflow_lifecycle
            and (lifecycle[0] == 0 or lifecycle[1] == 0)
        ) or (not require_workflow_lifecycle and lifecycle != (0, 0)):
            raise ContestDemoError(f"{mode} fence observer kind is invalid")
        if previous is not None:
            if (
                row["tick_us"] < previous["tick_us"]
                or row["observer_tick"] <= previous["observer_tick"]
                or row["observer_pid"] != previous["observer_pid"]
                or row["observer_lifecycle_id"]
                != previous["observer_lifecycle_id"]
                or row["observer_lifecycle_generation"]
                != previous["observer_lifecycle_generation"]
                or any(
                    row[name] < previous[name]
                    for name in _STORAGE_COUNTERS + _METADATA_COUNTERS
                )
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
        or mechanism["observer_lifecycle_id"]
        != before["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_id"]
        != after["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_generation"]
        != before["observer_lifecycle_generation"]
        or mechanism["observer_lifecycle_generation"]
        != after["observer_lifecycle_generation"]
        or mechanism["before_tick"] != before["observer_tick"]
        or mechanism["after_tick"] != after["observer_tick"]
    ):
        raise ContestDemoError(f"{label} mechanism observer does not match its fences")
    for name in _STORAGE_COUNTERS + _METADATA_COUNTERS:
        if (
            mechanism["raw_pair"]["before"][name] != before[name]
            or mechanism["raw_pair"]["after"][name] != after[name]
            or mechanism[name] != after[name] - before[name]
        ):
            raise ContestDemoError(f"{label} mechanism does not match its fences")
    if (
        mechanism["before_metadata_pending"] != before["metadata_pending"]
        or mechanism["after_metadata_pending"] != after["metadata_pending"]
    ):
        raise ContestDemoError(f"{label} mechanism pending state differs from fences")


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
        or mechanism["observer_lifecycle_id"]
        != settled["observer_lifecycle_id"]
        or mechanism["observer_lifecycle_generation"]
        != core_start["observer_lifecycle_generation"]
        or mechanism["observer_lifecycle_generation"]
        != settled["observer_lifecycle_generation"]
        or mechanism["before_tick"] != core_start["observer_tick"]
        or mechanism["after_tick"] > settled["observer_tick"]
    ):
        raise ContestDemoError(f"{label} mechanism observer is outside core interval")
    for name in _STORAGE_COUNTERS + _METADATA_COUNTERS:
        if (
            mechanism["raw_pair"]["before"][name] != core_start[name]
            or mechanism["raw_pair"]["after"][name] > settled[name]
        ):
            raise ContestDemoError(f"{label} mechanism is outside core interval")
    if (
        mechanism["before_metadata_pending"] != core_start["metadata_pending"]
        or mechanism["after_metadata_pending"] != 0
        or mechanism["raw_pair"]["after"]["metadata_dirty"]
        != mechanism["raw_pair"]["after"]["metadata_durable"]
    ):
        raise ContestDemoError(f"{label} core result is not durably settled")


def verify_showcase(
    path: Path,
    run_id: str,
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
    expected_nonce = int(run_id, 16)
    legacy = {
        key: _unique_match(lines, pattern, f"legacy {key}")
        for key, pattern in LEGACY_PATTERNS.items()
    }
    legacy_positions = [legacy[key][0] for key in LEGACY_PATTERNS]
    if legacy_positions != sorted(legacy_positions):
        raise ContestDemoError("legacy workflow markers are out of order")

    events: dict[str, list[dict[str, Any]]] = {"compat": [], "native": []}
    metrics: dict[str, dict[str, Any]] = {}
    fences: dict[str, list[dict[str, Any]]] = {
        "compat": [], "native": [], "runtime_probe": []
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
    if nonce_values != {expected_nonce}:
        raise ContestDemoError("showcase records are not bound to this run nonce")

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
            raise ContestDemoError(f"{mode} core duration does not match event timestamps")
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
            mode, lane_fences,
            ("E2E_START", "CORE_START", "ACK_SETTLED", "E2E_END"),
        )
        if not (
            lane_fences[0]["tick_us"] <= metric["end_to_end_started_us"]
            <= lane_fences[1]["tick_us"] <= ticks[0]
            <= ticks[-1] <= lane_fences[2]["tick_us"]
            <= lane_fences[3]["tick_us"] <= metric["end_to_end_finished_us"]
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
        raise ContestDemoError("compat lane did not execute the complete corpus")
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
        raise ContestDemoError("showcase oracle identity differs from the registered workload")
    if {
        compat["outcome_hash"], native["outcome_hash"], oracle["outcome_hash"],
        oracle["compat_hash"], oracle["native_hash"],
    } != {expected_hash}:
        raise ContestDemoError("compat/native outcomes do not match the Host oracle")

    expected_trace = (
        (1, "orchestrator", "INCIDENT"),
        (2, "sentinel", "DISCOVERED"),
        (3, "investigator", "HANDOFF"),
        (4, "recovery", "RECOVERY_COMMITTED"),
        (5, "orchestrator", "RECOVERED"),
    )
    if len(trace) != len(expected_trace):
        raise ContestDemoError("multi-Agent timeline is incomplete")
    for row, expected in zip(trace, expected_trace):
        if (row["sequence"], row["role"], row["event"]) != expected:
            raise ContestDemoError("multi-Agent timeline is out of contract")
    trace_ticks = [row["tick_us"] for row in trace]
    if trace_ticks != sorted(trace_ticks):
        raise ContestDemoError("multi-Agent timeline ticks are not monotonic")
    if runtime["duration_us"] != trace_ticks[-1] - trace_ticks[0]:
        raise ContestDemoError("runtime duration does not match the timeline")
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
        raise ContestDemoError("multi-Agent runtime counters violate the workflow oracle")

    if oracle["execution_order"] != run_record["order"]:
        raise ContestDemoError("oracle execution order differs from the run record")

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
        require_workflow_lifecycle=False,
    )
    for mode in ("compat", "native"):
        _check_core_mechanism(
            mechanisms[(mode, "core")], fences[mode][1], fences[mode][2],
            f"{mode} core",
        )
        if (
            metrics[mode]["workload_syscalls"]
            != mechanisms[(mode, "core")]["workload_syscalls"]
        ):
            raise ContestDemoError(
                f"{mode} workload syscall receipt differs from the kernel snapshot"
            )
        _check_mechanism_interval(
            mechanisms[(mode, "end_to_end")], fences[mode][0], fences[mode][3],
            f"{mode} end-to-end",
        )
    _check_mechanism_interval(
        mechanisms[("runtime_probe", "end_to_end")],
        fences["runtime_probe"][0], fences["runtime_probe"][1],
        "runtime probe",
    )
    lane_identity = (
        fences["compat"][0]["observer_pid"],
        fences["compat"][0]["observer_lifecycle_id"],
        fences["compat"][0]["observer_lifecycle_generation"],
    )
    if lane_identity[0] != compat["actor_pid"]:
        raise ContestDemoError("performance observer is not the workload actor")
    for mode in ("compat", "native"):
        if any(
            (
                row["observer_pid"],
                row["observer_lifecycle_id"],
                row["observer_lifecycle_generation"],
            )
            != lane_identity
            for row in fences[mode]
        ):
            raise ContestDemoError("compat/native lanes changed performance observer")
    workflow = mechanisms[("workflow", "end_to_end")]
    if (
        (
            workflow["observer_pid"],
            workflow["observer_lifecycle_id"],
            workflow["observer_lifecycle_generation"],
        )
        != lane_identity
        or workflow["before_tick"] <= max(
            fences["compat"][-1]["observer_tick"],
            fences["native"][-1]["observer_tick"],
        )
    ):
        raise ContestDemoError("workflow mechanism changed the signed observer")
    probe = mechanisms[("runtime_probe", "end_to_end")]
    if (
        probe["observer_pid"] == compat["actor_pid"]
        or probe["observer_lifecycle_id"] != 0
        or probe["observer_lifecycle_generation"] != 0
    ):
        raise ContestDemoError("runtime probe is not bound to the bootstrap observer")
    if any(
        probe[name] == 0
        for name in (
            "cow_shared_pages", "cow_copied_pages", "cow_fault_promotions",
            "exec_cache_hits", "exec_cache_misses", "exec_cache_shared_pages",
        )
    ):
        raise ContestDemoError("runtime probe did not exercise COW and exec reuse")

    for row in trace:
        row["offset_us"] = row["tick_us"] - trace_ticks[0]
        row.pop("nonce")
    for metric in metrics.values():
        metric.pop("nonce")
        metric.pop("mode")
    runtime.pop("nonce")
    oracle.pop("nonce")
    run_record.pop("nonce")
    nested_mechanisms: dict[str, dict[str, dict[str, Any]]] = {}
    for (mode, scope), mechanism in mechanisms.items():
        mechanism.pop("nonce")
        mechanism.pop("mode")
        mechanism.pop("scope")
        mechanism["buffers_per_epoch"] = _ratio(
            mechanism["epoch_buffers_staged"], mechanism["epoch_commits"]
        )
        nested_mechanisms.setdefault(mode, {})[scope] = mechanism
        mechanism["counter_scopes"] = dict(COUNTER_SCOPES)
    for mode_rows in fences.values():
        for row in mode_rows:
            row.pop("nonce")
            row.pop("mode")
    result = {
        "sample": run_record,
        "comparison": {
            "design": "same_kernel_same_guest_same_corpus",
            "execution_actor_pid": compat["actor_pid"],
            "timed_scope": "incident_to_verified_durable_outcome",
            "corpus_records": CORPUS_SIZE,
            "lanes": metrics,
            "ratios": {
                "compat_over_native_core_duration": _ratio(
                    compat["core_duration_us"], native["core_duration_us"]
                ),
                "compat_over_native_end_to_end_duration": _ratio(
                    compat["end_to_end_duration_us"],
                    native["end_to_end_duration_us"],
                ),
                "compat_over_native_workload_syscalls": _ratio(
                    compat["workload_syscalls"], native["workload_syscalls"]
                ),
                "compat_over_native_records_examined": _ratio(
                    compat["records_examined"], native["records_examined"]
                ),
            },
        },
        "outcome": {**oracle, "equal": True},
        "timeline": trace,
        "runtime": runtime,
        "observation": {
            "audit_records": _uint(legacy["audit"][1].group(1), "audit records"),
            "timeline_records": _uint(
                legacy["timeline"][1].group(1), "timeline records"
            ),
            "provenance_edges": _uint(
                legacy["provenance"][1].group(1), "provenance edges"
            ),
        },
        "fences": fences,
    }
    result["mechanisms"] = nested_mechanisms
    return result


def _median(values: list[int | float]) -> int | float:
    value = statistics.median(values)
    if float(value).is_integer():
        return int(value)
    return round(float(value), 3)


def _median_records(
    rows: list[dict[str, Any]], names: tuple[str, ...]
) -> dict[str, int | float]:
    return {name: _median([row[name] for row in rows]) for name in names}


def _median_optional_records(
    rows: list[dict[str, Any]], names: tuple[str, ...]
) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = {}
    for name in names:
        values = [row[name] for row in rows if row.get(name) is not None]
        result[name] = _median(values) if values else None
    return result


_T_CRITICAL_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def _rounded(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return round(value, 3)


def _mean_ci95(values: list[float]) -> dict[str, int | float] | None:
    if not values:
        return None
    estimate = statistics.fmean(values)
    if len(values) == 1:
        return {
            "estimate": _rounded(estimate),
            "low": _rounded(estimate),
            "high": _rounded(estimate),
        }
    standard_error = statistics.stdev(values) / len(values) ** 0.5
    critical = _T_CRITICAL_95.get(len(values) - 1, 1.96)
    margin = critical * standard_error
    return {
        "estimate": _rounded(estimate),
        "low": _rounded(estimate - margin),
        "high": _rounded(estimate + margin),
    }


def _paired_effect(
    rows: list[tuple[int | float, int | float]], label: str, unit: str
) -> dict[str, Any]:
    compat = [float(pair[0]) for pair in rows]
    native = [float(pair[1]) for pair in rows]
    deltas = [right - left for left, right in zip(compat, native)]
    ratios = [left / right for left, right in zip(compat, native) if right != 0]
    changes = [
        100.0 * (right - left) / left
        for left, right in zip(compat, native)
        if left != 0
    ]
    delta_ci = _mean_ci95(deltas)
    if delta_ci is None or delta_ci["low"] <= 0 <= delta_ci["high"]:
        direction = "uncertain"
    elif delta_ci["high"] < 0:
        direction = "lower"
    else:
        direction = "higher"
    return {
        "label": label,
        "unit": unit,
        "sample_count": len(rows),
        "compat_p50": _median([pair[0] for pair in rows]),
        "native_p50": _median([pair[1] for pair in rows]),
        "native_minus_compat": delta_ci,
        "compat_over_native": _mean_ci95(ratios),
        "native_change_pct": _mean_ci95(changes),
        "direction": direction,
    }


def _paired_effects(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    specs: tuple[tuple[str, str, str], ...],
) -> dict[str, dict[str, Any]]:
    effects: dict[str, dict[str, Any]] = {}
    for name, label, unit in specs:
        usable = [
            (compat[name], native[name])
            for compat, native in rows
            if compat.get(name) is not None and native.get(name) is not None
        ]
        if usable:
            effects[name] = _paired_effect(usable, label, unit)
            if name == "workload_syscalls":
                effects[name]["counter_scope"] = "observer_process"
            elif name in {
                "core_duration_us", "end_to_end_duration_us",
                "records_examined", "bytes_read",
            }:
                effects[name]["counter_scope"] = "guest_workload"
            else:
                effects[name]["counter_scope"] = "global"
    return effects


def campaign_aggregates(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        raise ContestDemoError("showcase campaign has no replayed samples")
    order_counts = {
        order: sum(sample["sample"]["order"] == order for sample in samples)
        for order in ("compat_then_native", "native_then_compat")
    }
    if len(set(order_counts.values())) != 1:
        raise ContestDemoError("showcase campaign is not AB/BA balanced")

    expected_outcome = samples[0]["outcome"].copy()
    expected_outcome.pop("execution_order")
    for sample in samples[1:]:
        outcome = sample["outcome"].copy()
        outcome.pop("execution_order")
        if outcome != expected_outcome:
            raise ContestDemoError("campaign outcomes differ between boots")

    lane_names = (
        "workload_syscalls", "records_examined", "bytes_read", "result_items",
        "outcome_hash", "core_duration_us", "end_to_end_duration_us",
    )
    lanes: dict[str, dict[str, Any]] = {}
    for mode in ("compat", "native"):
        lane_rows = [sample["comparison"]["lanes"][mode] for sample in samples]
        lane = _median_records(lane_rows, lane_names)
        lane["actor_pids"] = sorted({row["actor_pid"] for row in lane_rows})
        first_rows = [
            row for row, sample in zip(lane_rows, samples)
            if sample["sample"]["order"].startswith(mode)
        ]
        second_rows = [
            row for row, sample in zip(lane_rows, samples)
            if not sample["sample"]["order"].startswith(mode)
        ]
        lane["cold_core_duration_us"] = _median(
            [row["core_duration_us"] for row in first_rows]
        )
        lane["hot_core_duration_us"] = _median(
            [row["core_duration_us"] for row in second_rows]
        )
        lane["cold_end_to_end_duration_us"] = _median(
            [row["end_to_end_duration_us"] for row in first_rows]
        )
        lane["hot_end_to_end_duration_us"] = _median(
            [row["end_to_end_duration_us"] for row in second_rows]
        )
        lanes[mode] = lane

    mechanism_names = MECHANISM_COUNTERS + (
        "buffers_per_epoch", "directory_entries_per_probe",
        "virtio_requests_per_notification", "virtio_write_requests_per_batch",
        "virtio_read_requests_per_batch", "virtio_batched_request_share_pct",
        "metadata_coalescing_rate_pct", "metadata_requests_per_commit",
    )
    mechanisms: dict[str, dict[str, dict[str, Any]]] = {}
    for mode, scopes in samples[0]["mechanisms"].items():
        mechanisms[mode] = {}
        for scope in scopes:
            rows = [sample["mechanisms"][mode][scope] for sample in samples]
            item = _median_optional_records(rows, mechanism_names)
            item["counter_scope"] = "global"
            item["counter_scopes"] = dict(COUNTER_SCOPES)
            item["raw_cycle_ordering"] = {
                "ordered_samples": len(rows),
                "minimum_gap": min(row["raw_cycle_order_gap"] for row in rows),
                "median_gap": _median(
                    [row["raw_cycle_order_gap"] for row in rows]
                ),
                "unit": "raw_cycle_order_token",
            }
            item["virtio_notification_scope"] = "mixed_read_write"
            mechanisms[mode][scope] = item

    runtime_names = (
        "agents", "duration_us", "tool_calls", "dispatches", "wait_sleeps",
        "wait_wakeups", "records_examined", "denied_actions",
        "duplicate_actions", "recovery_side_effects",
    )
    runtime = _median_records([sample["runtime"] for sample in samples], runtime_names)
    observation = _median_records(
        [sample["observation"] for sample in samples],
        ("audit_records", "timeline_records", "provenance_edges"),
    )
    comparison = {
        "design": "same_kernel_same_guest_same_corpus",
        "timed_scope": {
            "core": "incident_to_verified_durable_outcome",
            "end_to_end": "quiescent_seed_core_cleanup_quiescent",
        },
        "corpus_records": CORPUS_SIZE,
        "order_balance": order_counts,
        "lanes": lanes,
        "ratios": {
            "compat_over_native_core_duration": _ratio(
                lanes["compat"]["core_duration_us"],
                lanes["native"]["core_duration_us"],
            ),
            "compat_over_native_end_to_end_duration": _ratio(
                lanes["compat"]["end_to_end_duration_us"],
                lanes["native"]["end_to_end_duration_us"],
            ),
            "compat_over_native_workload_syscalls": _ratio(
                lanes["compat"]["workload_syscalls"],
                lanes["native"]["workload_syscalls"],
            ),
            "compat_over_native_records_examined": _ratio(
                lanes["compat"]["records_examined"],
                lanes["native"]["records_examined"],
            ),
        },
        "paired_effects": _paired_effects(
            [
                (
                    sample["comparison"]["lanes"]["compat"],
                    sample["comparison"]["lanes"]["native"],
                )
                for sample in samples
            ],
            LANE_EFFECT_SPECS,
        ),
    }
    mechanism_effects = {
        scope: _paired_effects(
            [
                (
                    sample["mechanisms"]["compat"][scope],
                    sample["mechanisms"]["native"][scope],
                )
                for sample in samples
            ],
            MECHANISM_EFFECT_SPECS,
        )
        for scope in ("core", "end_to_end")
    }
    return {
        "comparison": comparison,
        "outcome": {**expected_outcome, "execution_orders": order_counts},
        "timeline": samples[0]["timeline"],
        "timeline_sample_id": samples[0]["sample"]["id"],
        "runtime": runtime,
        "observation": observation,
        "mechanisms": mechanisms,
        "mechanism_effects": mechanism_effects,
    }


def build_report(
    source_root: Path,
    lab_logs: list[Path],
    run_id: str,
    commit: str,
    elapsed_seconds: float,
    artifacts: list[Path],
) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id) or int(run_id, 16) == 0:
        raise ContestDemoError("run id must be 16 nonzero lowercase hex digits")
    if not COMMIT.fullmatch(commit):
        raise ContestDemoError("commit must be a full Git object id")
    if elapsed_seconds <= 0:
        raise ContestDemoError("elapsed time must be positive")
    if len(lab_logs) < 8 or len(lab_logs) % 2 != 0:
        raise ContestDemoError("showcase campaign requires an even set of at least 8 boots")
    if clean_source_identity(source_root) != commit:
        raise ContestDemoError("source identity changed during the demo")
    with tempfile.TemporaryDirectory(prefix="agentos-showcase-source-") as temporary:
        sample_before = _measurement_source_sample(
            source_root, commit, snapshot_root=Path(temporary)
        )
    sample_after = _measurement_source_sample(source_root, commit)
    if sample_before != sample_after:
        raise ContestDemoError("showcase source changed while validating the Guest log")
    if clean_source_identity(source_root) != commit:
        raise ContestDemoError("source identity changed during showcase validation")

    checked_logs = [
        _regular_file(path, f"showcase Guest log {index}")
        for index, path in enumerate(lab_logs, 1)
    ]
    samples = []
    for index, log in enumerate(checked_logs, 1):
        expected_order = (
            "compat_then_native" if index % 2 else "native_then_compat"
        )
        samples.append(
            verify_showcase(log, run_id, index, expected_order)
        )
    aggregates = campaign_aggregates(samples)
    artifact_paths = list(checked_logs)
    artifact_paths.extend(_regular_file(path, "showcase artifact") for path in artifacts)
    if len({path.resolve() for path in artifact_paths}) != len(artifact_paths):
        raise ContestDemoError("showcase artifact list contains duplicates")
    if len({path.name for path in artifact_paths}) != len(artifact_paths):
        raise ContestDemoError("showcase artifact basenames must be unique")
    expected_artifact_names = {"showcase-kernel"}
    for index in range(1, len(samples) + 1):
        sample_tag = f"{index:02d}"
        expected_artifact_names.update(
            {
                f"sample-{sample_tag}-qemu.log",
                f"sample-{sample_tag}-fs.img",
                f"sample-{sample_tag}-labdemo.elf",
            }
        )
    artifact_by_name = {path.name: path for path in artifact_paths}
    if set(artifact_by_name) != expected_artifact_names:
        missing = sorted(expected_artifact_names - set(artifact_by_name))
        extra = sorted(set(artifact_by_name) - expected_artifact_names)
        raise ContestDemoError(
            f"showcase artifacts do not match the build manifest; "
            f"missing={missing}, extra={extra}"
        )
    artifact_receipts = {
        name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for name, path in sorted(artifact_by_name.items())
    }
    build_manifest = {
        "schema_version": 1,
        "kind": "agentos-showcase-build-manifest",
        "run_id": run_id,
        "source_commit": commit,
        "kernel_artifact": "showcase-kernel",
        "samples": [
            {
                "sample_id": index,
                "order": sample["sample"]["order"],
                "kernel_artifact": "showcase-kernel",
                "guest_elf_artifact": f"sample-{index:02d}-labdemo.elf",
                "fs_image_artifact": f"sample-{index:02d}-fs.img",
                "guest_log_artifact": f"sample-{index:02d}-qemu.log",
            }
            for index, sample in enumerate(samples, 1)
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "run": {
            "id": run_id,
            "commit": commit,
            "qemu_boots": len(samples),
            "wall_seconds": round(elapsed_seconds, 3),
        },
        **aggregates,
        "measurement_protocol": {
            "sample_count": len(samples),
            "counter_scopes": dict(COUNTER_SCOPES),
            "quiescence_fence": {
                "stable_rounds": 2,
                "max_attempts": 16,
                "counter_scope": "field_scoped",
                "counter_scopes": dict(COUNTER_SCOPES),
                "converged_samples": len(samples),
            },
            "ordering": "balanced_AB_BA",
            "cold_hot_meaning": "first_or_second_lane_within_each_boot",
            "qemu_jobs": 1,
            "host_concurrency": "one_isolated_qemu_at_a_time",
            "observer_identity": {
                "workflow": "pid_plus_nonzero_lifecycle_id_generation",
                "system_probe": "signed_bootstrap_pid_plus_lifecycle_0_0",
            },
        },
        "samples": samples,
        "source_receipt": _source_receipt(sample_after),
        "build_manifest": build_manifest,
        "artifacts": artifact_receipts,
    }


def _fmt_duration(value: int | float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} s"
    if value >= 1_000:
        return f"{value / 1_000:.3f} ms"
    return f"{value:g} us"


def _fmt_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


def _fmt_decimal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt_value(value: int | float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "us":
        return _fmt_duration(value)
    if unit == "%":
        return f"{value:g}%"
    return f"{value:g} {unit}"


def _fmt_signed(value: int | float, unit: str) -> str:
    if unit == "us":
        return f"{value:+g} us"
    if unit == "%":
        return f"{value:+g}%"
    return f"{value:+g} {unit}"


def _fmt_ci(
    interval: dict[str, int | float] | None, unit: str, *, signed: bool = False
) -> str:
    if interval is None:
        return "n/a"
    formatter = _fmt_signed if signed else _fmt_value
    return "{} [{}，{}]".format(
        formatter(interval["estimate"], unit),
        formatter(interval["low"], unit),
        formatter(interval["high"], unit),
    )


def _fmt_ratio_ci(interval: dict[str, int | float] | None) -> str:
    if interval is None:
        return "n/a"
    return "{:.2f}x [{:.2f}，{:.2f}]".format(
        interval["estimate"], interval["low"], interval["high"]
    )


def _direction_label(direction: str) -> str:
    return {
        "lower": "Native 较低",
        "higher": "Native 较高",
        "uncertain": "区间跨 0",
    }[direction]


def _effect_rows(effects: dict[str, dict[str, Any]]) -> str:
    rows = []
    for effect in effects.values():
        unit = effect["unit"]
        rows.append(
            "<tr><th>{}</th><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _h(effect["label"]),
                _h(unit),
                _h(effect["counter_scope"]),
                _h(_fmt_value(effect["compat_p50"], unit)),
                _h(_fmt_value(effect["native_p50"], unit)),
                _h(_fmt_ci(effect["native_minus_compat"], unit, signed=True)),
                _h(_fmt_ratio_ci(effect["compat_over_native"])),
                _h(_direction_label(effect["direction"])),
            )
        )
    return "".join(rows)


def _effect_markdown_lines(effects: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| 指标 | 单位 | 计数口径 | Compat p50 | Native p50 | "
        "Native - Compat 均值 [95% CI] | Compat / Native 均值 [95% CI] | 方向 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for effect in effects.values():
        unit = effect["unit"]
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                effect["label"],
                unit,
                effect["counter_scope"],
                _fmt_value(effect["compat_p50"], unit),
                _fmt_value(effect["native_p50"], unit),
                _fmt_ci(effect["native_minus_compat"], unit, signed=True),
                _fmt_ratio_ci(effect["compat_over_native"]),
                _direction_label(effect["direction"]),
            )
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    mechanisms = report["mechanisms"]
    lines = [
        "# AgentOS 科研故障恢复实测",
        "",
        f"提交 `{report['run']['commit']}`，{report['run']['qemu_boots']} 次真实 "
        "QEMU 启动；同一内核、同一 Guest 程序与语料，每轮仅按预注册的 "
        "sample/order 重建工作负载镜像，AB/BA 等量换序。",
        "",
        "Raw cycle 仅验证 Guest 快照先后顺序；耗时、差值、比率和区间均来自 "
        "Guest 单调微秒时钟或内核计数器的启动内前后差分。",
        "",
        "## 同内核配对效果",
        "",
    ]
    lines.extend(_effect_markdown_lines(comparison["paired_effects"]))
    lines.extend(("", "## 核心区间机制效果", ""))
    lines.extend(_effect_markdown_lines(report["mechanism_effects"]["core"]))
    lines.extend(("", "## 端到端机制效果", ""))
    lines.extend(_effect_markdown_lines(report["mechanism_effects"]["end_to_end"]))
    lines.extend(
        (
            "",
            "## 底层机制绝对计数",
            "",
            "数值为 Guest ABI v2 快照的多启动中位数；除 workload_syscalls "
            "绑定 observer_process 外，其余内核计数为 global；notify/request 覆盖读写请求。",
            "",
            "| 模式 / 区间 | Workload syscall | 目录 block / entry | Epoch / buffer | 物理读 / 写 / flush | 跳过预读 | VirtIO notify / request | 读 batch / request | 写 batch / request / indirect | Metadata request / merged / commit | COW 共享 / 复制 / 提升 | Exec 命中 / 未命中 / 共享 / 驱逐 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for mode, scope in (
        ("compat", "core"), ("compat", "end_to_end"),
        ("native", "core"), ("native", "end_to_end"),
        ("workflow", "end_to_end"), ("runtime_probe", "end_to_end"),
    ):
        item = mechanisms[mode][scope]
        lines.append(
            "| {} / {} | {} | {} / {} | {} / {} | {} / {} / {} | {} | {} / {} | "
            "{} / {} | {} / {} / {} | {} / {} / {} | {} / {} / {} | {} / {} / {} / {} |".format(
                mode, scope,
                item["workload_syscalls"],
                item["directory_block_probes"], item["directory_entries_examined"],
                item["epoch_commits"], _fmt_decimal(item["buffers_per_epoch"]),
                item["physical_reads"], item["physical_writes"],
                item["durable_flushes"], item["overwrite_prereads_skipped"],
                item["virtio_notifications"], item["virtio_submitted_requests"],
                item["virtio_read_batch_calls"], item["virtio_batched_read_requests"],
                item["virtio_write_batch_calls"], item["virtio_batched_write_requests"],
                item["virtio_indirect_write_batch_calls"],
                item["metadata_requests"], item["metadata_coalesced"],
                item["metadata_commits"], item["cow_shared_pages"],
                item["cow_copied_pages"], item["cow_fault_promotions"],
                item["exec_cache_hits"], item["exec_cache_misses"],
                item["exec_cache_shared_pages"], item["exec_cache_evictions"],
            )
        )
    lines.extend(
        (
            "",
            "## 8 boot 原始配对",
            "",
            "| Boot | 顺序 | Compat core (us) | Native core (us) | Native - Compat (us) | Compat / Native | 目录 probes C/N | VirtIO notify C/N | 批量读 request C/N | 物理读 C/N | 跳过预读 C/N | Metadata merged C/N |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for sample in report["samples"]:
        compat = sample["comparison"]["lanes"]["compat"]
        native = sample["comparison"]["lanes"]["native"]
        compat_mechanism = sample["mechanisms"]["compat"]["end_to_end"]
        native_mechanism = sample["mechanisms"]["native"]["end_to_end"]
        lines.append(
            "| {} | {} | {} | {} | {:+d} | {:.2f}x | {} / {} | {} / {} | "
            "{} / {} | {} / {} | {} / {} | {} / {} |".format(
                sample["sample"]["id"],
                sample["sample"]["order"].replace("_then_", " -> "),
                compat["core_duration_us"], native["core_duration_us"],
                native["core_duration_us"] - compat["core_duration_us"],
                compat["core_duration_us"] / native["core_duration_us"],
                compat_mechanism["directory_block_probes"],
                native_mechanism["directory_block_probes"],
                compat_mechanism["virtio_notifications"],
                native_mechanism["virtio_notifications"],
                compat_mechanism["virtio_batched_read_requests"],
                native_mechanism["virtio_batched_read_requests"],
                compat_mechanism["physical_reads"],
                native_mechanism["physical_reads"],
                compat_mechanism["overwrite_prereads_skipped"],
                native_mechanism["overwrite_prereads_skipped"],
                compat_mechanism["metadata_coalesced"],
                native_mechanism["metadata_coalesced"],
            )
        )
    lines.extend(
        (
            "",
            "## 多 Agent 时间线",
        "",
        "| +us | 角色 | 事件 |",
        "| ---: | --- | --- |",
        )
    )
    lines.extend(
        f"| {item['offset_us']} | {item['role']} | {item['event']} |"
        for item in report["timeline"]
    )
    lines.extend(
        (
            "",
            f"最终状态 `{report['outcome']['final_status']}`；Compat 与 Native 的 "
            f"outcome hash 均为 `{report['outcome']['outcome_hash']}`。",
        )
    )
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    compat = comparison["lanes"]["compat"]
    native = comparison["lanes"]["native"]
    ratios = comparison["ratios"]
    runtime = report["runtime"]
    outcome = report["outcome"]
    mechanisms = report["mechanisms"]
    compat_core = mechanisms["compat"]["core"]
    native_core = mechanisms["native"]["core"]
    compat_e2e = mechanisms["compat"]["end_to_end"]
    native_e2e = mechanisms["native"]["end_to_end"]
    probe = mechanisms["runtime_probe"]["end_to_end"]
    timeline = "".join(
        '<li><span class="time">+{offset}</span>'
        '<span class="role {role}">{role}</span><strong>{event}</strong>'
        '<span class="dot" aria-hidden="true"></span></li>'.format(
            offset=_h(item["offset_us"]),
            role=_h(item["role"]),
            event=_h(item["event"].replace("_", " ").title()),
        )
        for item in report["timeline"]
    )
    lane_effect_rows = _effect_rows(comparison["paired_effects"])
    mechanism_core_effect_rows = _effect_rows(
        report["mechanism_effects"]["core"]
    )
    mechanism_e2e_effect_rows = _effect_rows(
        report["mechanism_effects"]["end_to_end"]
    )
    core_latency_effect = comparison["paired_effects"]["core_duration_us"]
    e2e_latency_effect = comparison["paired_effects"]["end_to_end_duration_us"]
    mechanism_rows = "".join(
            "<tr><th>{}</th><td>{}</td><td>{} / {}</td><td>{} / {}</td>"
            "<td>{} / {} / {}</td><td>{}</td><td>{} / {}</td><td>{} / {}</td>"
            "<td>{} / {} / {}</td><td>{} / {} / {} ({})</td>"
            "<td>{} / {} / {}</td><td>{} / {} / {} / {}</td></tr>".format(
                _h(f"{mode} / {scope}"),
                _h(item["workload_syscalls"]),
                _h(item["directory_block_probes"]),
                _h(item["directory_entries_examined"]),
                _h(item["epoch_commits"]),
                _h(_fmt_decimal(item["buffers_per_epoch"])),
                _h(item["physical_reads"]),
                _h(item["physical_writes"]),
                _h(item["durable_flushes"]),
                _h(item["overwrite_prereads_skipped"]),
                _h(item["virtio_notifications"]),
                _h(item["virtio_submitted_requests"]),
                _h(item["virtio_read_batch_calls"]),
                _h(item["virtio_batched_read_requests"]),
                _h(item["virtio_write_batch_calls"]),
                _h(item["virtio_batched_write_requests"]),
                _h(item["virtio_indirect_write_batch_calls"]),
                _h(item["metadata_requests"]),
                _h(item["metadata_coalesced"]),
                _h(item["metadata_commits"]),
                _h(_fmt_value(item["metadata_coalescing_rate_pct"], "%")),
                _h(item["cow_shared_pages"]),
                _h(item["cow_copied_pages"]),
                _h(item["cow_fault_promotions"]),
                _h(item["exec_cache_hits"]),
                _h(item["exec_cache_misses"]),
                _h(item["exec_cache_shared_pages"]),
                _h(item["exec_cache_evictions"]),
            )
            for mode, scope, item in (
                (mode, scope, mechanisms[mode][scope])
                for mode, scope in (
                    ("compat", "core"), ("compat", "end_to_end"),
                    ("native", "core"), ("native", "end_to_end"),
                    ("workflow", "end_to_end"),
                    ("runtime_probe", "end_to_end"),
                )
            )
        )
    raw_rows = "".join(
        "<tr><th>{}</th><td>{}</td><td>{}</td><td>{}</td><td>{:+d} us</td>"
        "<td>{:.2f}x</td><td>{} / {}</td><td>{} / {}</td>"
        "<td>{} / {}</td><td>{} / {}</td><td>{} / {}</td>"
        "<td>{} / {}</td></tr>".format(
            _h(sample["sample"]["id"]),
            _h(sample["sample"]["order"].replace("_then_", " → ")),
            _h(sample["comparison"]["lanes"]["compat"]["core_duration_us"]),
            _h(sample["comparison"]["lanes"]["native"]["core_duration_us"]),
            sample["comparison"]["lanes"]["native"]["core_duration_us"]
            - sample["comparison"]["lanes"]["compat"]["core_duration_us"],
            sample["comparison"]["lanes"]["compat"]["core_duration_us"]
            / sample["comparison"]["lanes"]["native"]["core_duration_us"],
            _h(sample["mechanisms"]["compat"]["end_to_end"]["directory_block_probes"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["directory_block_probes"]),
            _h(sample["mechanisms"]["compat"]["end_to_end"]["virtio_notifications"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["virtio_notifications"]),
            _h(sample["mechanisms"]["compat"]["end_to_end"]["virtio_batched_read_requests"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["virtio_batched_read_requests"]),
            _h(sample["mechanisms"]["compat"]["end_to_end"]["physical_reads"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["physical_reads"]),
            _h(sample["mechanisms"]["compat"]["end_to_end"]["overwrite_prereads_skipped"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["overwrite_prereads_skipped"]),
            _h(sample["mechanisms"]["compat"]["end_to_end"]["metadata_coalesced"]),
            _h(sample["mechanisms"]["native"]["end_to_end"]["metadata_coalesced"]),
        )
        for sample in report["samples"]
    )
    data_json = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    embedded_json = (
        data_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentOS 科研故障恢复实测</title><style>
:root{{--ink:#172126;--muted:#5f6d73;--line:#d9e0e2;--paper:#fff;--soft:#f4f7f6;--teal:#087d75;--blue:#174d68;--green:#16734a;font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;letter-spacing:0}}
*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:var(--paper);line-height:1.5}}header{{border-bottom:1px solid var(--line);background:#f8faf9}}.wrap{{width:min(calc(100% - 40px),1260px);margin:auto}}header .wrap{{display:flex;align-items:center;justify-content:space-between;min-height:68px;gap:24px}}.brand{{font-weight:800;color:var(--blue)}}.run{{font:13px ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere}}main{{padding:26px 0 54px}}h1{{font-size:40px;line-height:1.12;margin:4px 0 8px;letter-spacing:0;max-width:900px;overflow-wrap:anywhere}}.lead{{color:var(--muted);max-width:1040px;margin:0;overflow-wrap:anywhere}}.eyebrow{{font-size:12px;font-weight:800;color:var(--teal);margin:0}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-block:1px solid var(--line);margin:22px 0}}.metric{{padding:13px 15px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);min-width:0}}.metric:nth-child(4n){{border-right:0}}.metric:nth-last-child(-n+4){{border-bottom:0}}.metric span{{display:block;color:var(--muted);font-size:11px}}.metric strong{{display:block;font-size:21px;margin-top:2px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}.metric small{{display:block;color:var(--teal);font-size:11px;overflow-wrap:anywhere}}.section{{margin-top:32px}}.section-head{{display:flex;justify-content:space-between;align-items:end;gap:30px;margin-bottom:10px}}h2{{margin:0;font-size:20px}}.section-head p{{margin:0;color:var(--muted);font-size:12px}}.table{{overflow:auto;border-block:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;min-width:960px}}th,td{{padding:11px 13px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}th:first-child{{text-align:left}}thead th{{font-size:11px;color:var(--muted);background:var(--soft)}}tbody tr:last-child>*{{border:0}}.timeline{{list-style:none;padding:20px 0 0;margin:0;position:relative;display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}.timeline:before{{content:"";position:absolute;top:31px;left:10%;right:10%;height:2px;background:var(--line)}}.timeline li{{position:relative;padding-top:30px;min-width:0;text-align:center}}.timeline .dot{{position:absolute;top:5px;left:50%;transform:translateX(-50%);width:13px;height:13px;border:3px solid var(--paper);border-radius:50%;background:var(--teal);box-shadow:0 0 0 1px var(--teal)}}.timeline span,.timeline strong{{display:block}}.timeline .time{{font:12px ui-monospace,monospace;color:var(--muted)}}.timeline .role{{font-size:12px;color:var(--teal);margin-top:5px}}.timeline strong{{font-size:14px;overflow-wrap:anywhere}}.runtime{{display:grid;grid-template-columns:repeat(6,1fr);border-block:1px solid var(--line)}}.runtime div{{padding:13px;border-right:1px solid var(--line);min-width:0}}.runtime div:last-child{{border:0}}.runtime span{{display:block;color:var(--muted);font-size:11px}}.runtime b{{font-size:19px;overflow-wrap:anywhere}}.outcome{{border-left:4px solid var(--green);background:#edf7f2;padding:15px 18px}}.outcome strong{{font-size:20px}}.outcome code{{display:block;margin-top:5px;overflow-wrap:anywhere}}footer{{border-top:1px solid var(--line);padding:18px 0;color:var(--muted);font-size:12px}}@media(max-width:900px){{h1{{font-size:32px}}.metrics{{grid-template-columns:1fr 1fr}}.metric,.metric:nth-child(4n){{border-right:0;border-bottom:1px solid var(--line)}}.metric:nth-child(odd){{border-right:1px solid var(--line)}}.metric:nth-last-child(-n+2){{border-bottom:0}}.runtime{{grid-template-columns:repeat(3,1fr)}}.timeline{{grid-template-columns:1fr}}.timeline:before{{display:none}}.timeline li{{padding:8px 0 8px 24px;border-bottom:1px solid var(--line);text-align:left}}.timeline .dot{{top:14px;left:0;transform:none}}header .wrap,.section-head{{align-items:flex-start;flex-direction:column;padding-block:13px}}}}
</style></head><body><header><div class="wrap"><div><div class="brand">AgentOS-uCore</div><div>科研故障恢复实测</div></div><div class="run">run {_h(report['run']['id'])} · commit {_h(report['run']['commit'][:12])}</div></div></header>
<main class="wrap"><p class="eyebrow">同内核 · {_h(report['run']['qemu_boots'])} boot 中位数 · AB/BA 等量换序</p><h1>AgentOS 科研故障恢复测量</h1><p class="lead">每次启动由同一 Orchestrator 在 {_h(comparison['corpus_records'])} 条相同工件上运行 Compat 与 Native。端到端包含 seed、恢复、cleanup 和静稳栅栏；core 仅覆盖故障发现到可持久验证结果。workload_syscalls 绑定 observer_process，其余内核计数为 global 区间差分；首/次路径分组只暴露顺序效应，不作缓存因果归因。</p>
<section class="metrics" aria-label="实测数值">
<div class="metric"><span>核心耗时 p50</span><strong>{_h(_fmt_duration(compat['core_duration_us']))} → {_h(_fmt_duration(native['core_duration_us']))}</strong><small>Compat → Native</small></div>
<div class="metric"><span>核心耗时比</span><strong>{_h(_fmt_ratio(ratios['compat_over_native_core_duration']))}</strong><small>{_h(_fmt_ci(core_latency_effect['native_change_pct'], '%', signed=True))}</small></div>
<div class="metric"><span>端到端耗时 p50</span><strong>{_h(_fmt_duration(compat['end_to_end_duration_us']))} → {_h(_fmt_duration(native['end_to_end_duration_us']))}</strong><small>Compat → Native</small></div>
<div class="metric"><span>端到端耗时比</span><strong>{_h(_fmt_ratio(ratios['compat_over_native_end_to_end_duration']))}</strong><small>{_h(_fmt_ci(e2e_latency_effect['native_change_pct'], '%', signed=True))}</small></div>
<div class="metric"><span>目录块探测 p50</span><strong>{_h(compat_e2e['directory_block_probes'])} → {_h(native_e2e['directory_block_probes'])}</strong><small>{_h(compat_e2e['directory_entries_examined'])} → {_h(native_e2e['directory_entries_examined'])} entries</small></div>
<div class="metric"><span>物理读 / 写 / Flush p50</span><strong>{_h(compat_e2e['physical_reads'])}/{_h(compat_e2e['physical_writes'])}/{_h(compat_e2e['durable_flushes'])} → {_h(native_e2e['physical_reads'])}/{_h(native_e2e['physical_writes'])}/{_h(native_e2e['durable_flushes'])}</strong><small>覆盖写跳过预读 {_h(compat_e2e['overwrite_prereads_skipped'])} → {_h(native_e2e['overwrite_prereads_skipped'])}</small></div>
<div class="metric"><span>VirtIO notify / request p50</span><strong>{_h(compat_e2e['virtio_notifications'])}/{_h(compat_e2e['virtio_submitted_requests'])} → {_h(native_e2e['virtio_notifications'])}/{_h(native_e2e['virtio_submitted_requests'])}</strong><small>混合读写 I/O</small></div>
<div class="metric"><span>VirtIO 读批量 p50</span><strong>{_h(compat_e2e['virtio_read_batch_calls'])}/{_h(compat_e2e['virtio_batched_read_requests'])} → {_h(native_e2e['virtio_read_batch_calls'])}/{_h(native_e2e['virtio_batched_read_requests'])}</strong><small>batch / request</small></div>
<div class="metric"><span>Metadata 合并率 p50</span><strong>{_h(_fmt_value(compat_e2e['metadata_coalescing_rate_pct'], '%'))} → {_h(_fmt_value(native_e2e['metadata_coalescing_rate_pct'], '%'))}</strong><small>{_h(compat_e2e['metadata_requests'])} → {_h(native_e2e['metadata_requests'])} requests</small></div>
<div class="metric"><span>VirtIO 写批量 p50</span><strong>{_h(compat_e2e['virtio_write_batch_calls'])}/{_h(compat_e2e['virtio_batched_write_requests'])} → {_h(native_e2e['virtio_write_batch_calls'])}/{_h(native_e2e['virtio_batched_write_requests'])}</strong><small>间接描述符批次 {_h(compat_e2e['virtio_indirect_write_batch_calls'])} → {_h(native_e2e['virtio_indirect_write_batch_calls'])}</small></div>
<div class="metric"><span>COW probe p50</span><strong>{_h(probe['cow_shared_pages'])} / {_h(probe['cow_copied_pages'])}</strong><small>共享 / 复制，提升 {_h(probe['cow_fault_promotions'])}</small></div>
<div class="metric"><span>Exec RX probe p50</span><strong>{_h(probe['exec_cache_hits'])} / {_h(probe['exec_cache_misses'])}</strong><small>命中 / 未命中，共享 {_h(probe['exec_cache_shared_pages'])}</small></div>
</section>
<section class="section"><div class="section-head"><h2>同内核配对效果</h2><p>绝对值、差值、比率和 95% 配对 t 区间</p></div><div class="table"><table><thead><tr><th>指标</th><th>单位</th><th>计数口径</th><th>Compat p50</th><th>Native p50</th><th>Native - Compat 均值 [95% CI]</th><th>Compat / Native 均值 [95% CI]</th><th>方向</th></tr></thead><tbody>{lane_effect_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>核心区间机制效果</h2><p>Guest 快照原始计数差分，8 boot 配对统计</p></div><div class="table"><table><thead><tr><th>指标</th><th>单位</th><th>计数口径</th><th>Compat p50</th><th>Native p50</th><th>Native - Compat 均值 [95% CI]</th><th>Compat / Native 均值 [95% CI]</th><th>方向</th></tr></thead><tbody>{mechanism_core_effect_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>端到端机制效果</h2><p>seed、恢复、cleanup 和静稳栅栏</p></div><div class="table"><table><thead><tr><th>指标</th><th>单位</th><th>计数口径</th><th>Compat p50</th><th>Native p50</th><th>Native - Compat 均值 [95% CI]</th><th>Compat / Native 均值 [95% CI]</th><th>方向</th></tr></thead><tbody>{mechanism_e2e_effect_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>底层机制绝对计数</h2><p>workload_syscalls 为 observer_process，其余为 global；notify/request 覆盖混合读写</p></div><div class="table"><table><thead><tr><th>模式 / 区间</th><th>Workload syscall</th><th>目录 block / entry</th><th>Epoch / buffer</th><th>物理读 / 写 / flush</th><th>跳过预读</th><th>VirtIO notify / request</th><th>读 batch / request</th><th>写 batch / request / indirect</th><th>Metadata request / merged / commit</th><th>COW 共享 / 复制 / 提升</th><th>Exec 命中 / 未命中 / 共享 / 驱逐</th></tr></thead><tbody>{mechanism_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>8 boot 原始配对</h2><p>每行来自一次 Guest 启动内的 ABI v2 前后快照</p></div><div class="table"><table><thead><tr><th>Boot</th><th>顺序</th><th>Compat core us</th><th>Native core us</th><th>差值</th><th>比率</th><th>目录 probes C/N</th><th>VirtIO notify C/N</th><th>批量读 requests C/N</th><th>物理读 C/N</th><th>跳过预读 C/N</th><th>Metadata merged C/N</th></tr></thead><tbody>{raw_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>多 Agent 恢复时间线</h2><p>incident → sentinel → investigator → recovery</p></div><ol class="timeline">{timeline}</ol></section>
<section class="section"><div class="section-head"><h2>内核运行观测</h2><p>同一实测 workflow 的三角色运行计数</p></div><div class="runtime"><div><span>Agent</span><b>{runtime['agents']}</b></div><div><span>Tool calls</span><b>{runtime['tool_calls']}</b></div><div><span>Dispatch</span><b>{runtime['dispatches']}</b></div><div><span>Wait sleep / wake</span><b>{runtime['wait_sleeps']} / {runtime['wait_wakeups']}</b></div><div><span>拒绝越权</span><b>{runtime['denied_actions']}</b></div><div><span>重复 / 副作用</span><b>{runtime['duplicate_actions']} / {runtime['recovery_side_effects']}</b></div></div></section>
<section class="section outcome"><span>结果一致性 · 最终科研工件状态</span><strong>{_h(outcome['final_status'])}</strong><code>outcome hash {_h(outcome['outcome_hash'])}</code></section>
<script type="application/json" id="agentos-live-data">{embedded_json}</script></main><footer><div class="wrap">{_h(report['run']['qemu_boots'])} 次真实 RISC-V QEMU 启动 · wall {_h(report['run']['wall_seconds'])} s · 原始日志与镜像摘要已嵌入数据</div></footer></body></html>"""


def publish(report: dict[str, Any], output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise ContestDemoError("output directory must not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in REMOVED_PUBLISHED_FILES:
        obsolete = output_dir / name
        if obsolete.is_symlink() or obsolete.is_file():
            obsolete.unlink()
        elif obsolete.exists():
            raise ContestDemoError(f"obsolete output is unsafe: {name}")
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(output_dir / "summary.json", serialized.encode("utf-8"))
    _atomic_write(output_dir / "report.md", render_markdown(report).encode("utf-8"))
    _atomic_write(output_dir / "index.html", render_html(report).encode("utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("identity", help="print a clean source identity")
    identity.add_argument("--root", type=Path, required=True)
    render = commands.add_parser("render", help="verify and render a balanced Guest campaign")
    render.add_argument("--source-root", type=Path, required=True)
    render.add_argument("--lab-log", action="append", type=Path, required=True)
    render.add_argument("--run-id", required=True)
    render.add_argument("--commit", required=True)
    render.add_argument("--elapsed-seconds", type=float, required=True)
    render.add_argument("--artifact", action="append", type=Path, default=[])
    render.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "identity":
            print(clean_source_identity(args.root))
            return 0
        report = build_report(
            args.source_root,
            args.lab_log,
            args.run_id,
            args.commit,
            args.elapsed_seconds,
            args.artifact,
        )
        publish(report, args.output_dir)
    except (ContestDemoError, OSError, UnicodeError, ValueError) as error:
        raise SystemExit(f"contest demo failed: {error}") from error
    lanes = report["comparison"]["lanes"]
    ratios = report["comparison"]["ratios"]
    print(
        "[contest-demo] compat_e2e={}us native_e2e={}us ratio={}".format(
            lanes["compat"]["end_to_end_duration_us"],
            lanes["native"]["end_to_end_duration_us"],
            _fmt_ratio(ratios["compat_over_native_end_to_end_duration"]),
        )
    )
    print(
        "[contest-demo] records compat={} native={} outcome_hash={}".format(
            lanes["compat"]["records_examined"],
            lanes["native"]["records_examined"],
            report["outcome"]["outcome_hash"],
        )
    )
    print(f"[contest-demo] dashboard: {(args.output_dir / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
