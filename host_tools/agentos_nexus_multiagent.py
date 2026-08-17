#!/usr/bin/env python3
"""Task-independent Nexus multi-Agent Harness.

Every Agent executes the same event-driven loop.  Configuration supplies its
capabilities, tools, prompt, and quotas; names are diagnostic labels only.
The Host mirrors the kernel Task/Artifact invariants so provider output never
acquires authority by being parsed as text.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import threading
import time
from typing import Callable, Final, Mapping, Sequence

_HOST_TOOLS_DIR = Path(__file__).resolve().parent
if str(_HOST_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_TOOLS_DIR))

try:
    import agentos_nexus_dev as development
    import agentos_harness_progress as harness_progress
    import agentos_native_task_channel as native_task
    import agentos_workspace as workspace
    import guest_llm_relay as relay
except ModuleNotFoundError:  # pragma: no cover
    from . import agentos_nexus_dev as development
    from . import agentos_harness_progress as harness_progress
    from . import agentos_native_task_channel as native_task
    from . import agentos_workspace as workspace
    from . import guest_llm_relay as relay


# One reserved workflow process is the persistent control Guest.  The kernel's
# eight-process workflow quota therefore leaves seven identities for configured
# Agents and controlled Providers.
MAX_AGENTS: Final = 7
MAX_TASKS: Final = 64
MAX_ARTIFACT_BYTES: Final = 64 * 1024
MAX_ARTIFACTS: Final = 128
MAX_MODEL_PROJECTION_BYTES: Final = 12 * 1024
MAX_ROUNDS: Final = 64
DEFAULT_HEARTBEAT_SECONDS: Final = 0.25
# Execution Contract nodes are single-success operations.  This mirrors the
# frozen 24-node plan installed by agentharness_ucore and lets the model stop
# seeing a tool before the Guest would reject an over-budget delegation.
CONTRACT_TOOL_BUDGET: Final = {
    "delegate_task": 3,
    "search_files": 3,
    "read_file": 3,
    "write_file": 4,
    "apply_patch": 3,
    "build_ucore_program": 5,
    "run_ucore_program": 3,
}

CAPABILITIES: Final = frozenset(
    (
        "READ_CONTEXT",
        "READ_WORKSPACE",
        "WRITE_WORKSPACE",
        "BUILD",
        "RUN",
        "ORCHESTRATE",
        "SHARE_ARTIFACT",
        "PREFETCH",
    )
)
TOOLS: Final = frozenset(
    (
        "search_files",
        "read_file",
        "write_file",
        "apply_patch",
        "build_ucore_program",
        "run_ucore_program",
    )
)
TOOL_CAPABILITY: Final = {
    "search_files": "READ_WORKSPACE",
    "read_file": "READ_WORKSPACE",
    "write_file": "WRITE_WORKSPACE",
    "apply_patch": "WRITE_WORKSPACE",
    "build_ucore_program": "BUILD",
    "run_ucore_program": "RUN",
}
SHAREABLE_KINDS: Final = frozenset(
    (
        "user",
        "tool",
        "private_summary",
        "file",
        "search",
        "patch",
        "build_diagnostic",
        "run_log",
        "test_result",
        "subtask_report",
        "team_summary",
        "final",
    )
)
RESULT_KINDS: Final = frozenset(("final", "subtask_report", "team_summary"))
OBSERVABILITY_EVENT: Final = {
    "task_delegated": "TASK_DELEGATED",
    "task_claimed": "TASK_CLAIMED",
    "task_completed": "TASK_COMPLETED",
    "artifact_accepted": "ARTIFACT_SHARED",
    "summary": "SUMMARY_COMMITTED",
}


class HarnessError(ValueError):
    """Fail-closed validation error."""


class KernelStatusMonitor:
    """Poll bounded Guest snapshots through the serialized native channel."""

    def __init__(
        self,
        channel: native_task.NativeTaskChannel,
        events: harness_progress.HarnessEventBus,
        interval: float,
    ) -> None:
        if not 0.25 <= interval <= 10.0:
            raise HarnessError("status_interval_invalid")
        self.channel = channel
        self.events = events
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="nexus-kernel-status", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                status = self.channel.status()
            except native_task.NativeTaskChannelError as error:
                if not self._stop.is_set():
                    self.events.emit(
                        "kernel", "kernel_status_failed",
                        "Guest status snapshot failed",
                        detail=_bounded_utf8(str(error), 160),
                    )
                return
            self.events.emit(
                "kernel", "kernel_status",
                (
                    f"Guest tick {status['tick']}; active tasks "
                    f"{status['tasks_active']}; SQ/CQ "
                    f"{status['sq_depth']}/{status['cq_depth']}; Artifacts "
                    f"{status['artifact_count']}; Catalog "
                    f"{status['catalog_state']}/{status['catalog_candidates']}"
                ),
                **status,
            )
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=7.0)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_utf8(value: str, maximum: int) -> str:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= maximum:
        return value
    marker = b"\n...[truncated]"
    raw = raw[: maximum - len(marker)]
    while raw:
        try:
            return raw.decode("utf-8") + marker.decode("ascii")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return marker.decode("ascii")


def _model_projection_json(projection: Mapping[str, object]) -> str:
    """Encode a valid bounded projection while retaining newest evidence first."""

    value = json.loads(_canonical(projection).decode("utf-8"))
    artifacts = value.get("artifacts")
    context = value.get("context")
    shared = value.get("shared_context")
    if isinstance(artifacts, list):
        # AgentLoop orders these newest first.  Older bodies remain available by
        # handle, but the next action must see the latest build/run receipt.
        del artifacts[3:]
    if isinstance(context, list):
        value["context"] = context[-8:]
    if isinstance(shared, list):
        value["shared_context"] = shared[-4:]
    task = value.get("task")
    if isinstance(task, dict):
        for key, maximum in (("objective", 1536), ("input_artifact", 768)):
            if isinstance(task.get(key), str):
                task[key] = _bounded_utf8(task[key], maximum)
    if isinstance(value.get("goal"), str):
        value["goal"] = _bounded_utf8(value["goal"], 1536)
    agent = value.get("agent")
    if isinstance(agent, dict) and isinstance(agent.get("system_prompt"), str):
        agent["system_prompt"] = _bounded_utf8(agent["system_prompt"], 768)

    while True:
        encoded = _canonical(value)
        if len(encoded) <= MAX_MODEL_PROJECTION_BYTES:
            return encoded.decode("utf-8")
        artifacts = value.get("artifacts")
        if isinstance(artifacts, list):
            shrunk = False
            for artifact in reversed(artifacts):
                if not isinstance(artifact, dict):
                    continue
                content = artifact.get("content")
                if isinstance(content, str) and len(content.encode("utf-8")) > 512:
                    artifact["content"] = _bounded_utf8(
                        content, max(512, len(content.encode("utf-8")) - 512)
                    )
                    artifact["truncated"] = True
                    shrunk = True
                    break
            if shrunk:
                continue
            if len(artifacts) > 1:
                artifacts.pop()
                continue
        context = value.get("context")
        if isinstance(context, list) and len(context) > 3:
            del context[0]
            continue
        shared = value.get("shared_context")
        if isinstance(shared, list) and shared:
            del shared[0]
            continue
        # This fallback remains valid JSON and retains task identity plus the
        # newest sealed evidence metadata.  It should only be reachable after
        # unusually large labels or policy text.
        minimal = {
            "goal": _bounded_utf8(str(value.get("goal", "")), 512),
            "task": value.get("task"),
            "context": (value.get("context") or [])[-2:],
            "artifacts": (value.get("artifacts") or [])[:1],
            "contract_tool_remaining": value.get("contract_tool_remaining", {}),
        }
        encoded = _canonical(minimal)
        if len(encoded) > MAX_MODEL_PROJECTION_BYTES:
            artifact = minimal["artifacts"]
            if isinstance(artifact, list) and artifact and isinstance(artifact[0], dict):
                artifact[0].pop("content", None)
            task = minimal.get("task")
            if isinstance(task, dict):
                task["objective"] = _bounded_utf8(str(task.get("objective", "")), 512)
                task["input_artifact"] = ""
            encoded = _canonical(minimal)
        if len(encoded) > MAX_MODEL_PROJECTION_BYTES:
            raise HarnessError("model_projection_unbounded")
        return encoded.decode("utf-8")


def _parse_workspace_manifest(content: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    lines = content.splitlines()
    if not lines or lines[0] != "workspace_manifest_v1":
        raise HarnessError("workspace_manifest_malformed")
    header: dict[str, int] = {}
    entries: dict[int, dict[str, object]] = {}
    for line in lines[1:]:
        if "=" not in line:
            raise HarnessError("workspace_manifest_malformed")
        key, value = line.split("=", 1)
        match = re.fullmatch(
            r"entry\[([1-9][0-9]*)\]\.(object_id|path|revision|size|kind)", key
        )
        if match:
            index = int(match.group(1))
            entries.setdefault(index, {})[match.group(2)] = value
            continue
        if key not in {"cursor", "next_cursor", "entry_count", "eof"}:
            raise HarnessError("workspace_manifest_malformed")
        if not re.fullmatch(r"[0-9]+", value):
            raise HarnessError("workspace_manifest_malformed")
        header[key] = int(value)
    if set(header) != {"cursor", "next_cursor", "entry_count", "eof"}:
        raise HarnessError("workspace_manifest_malformed")
    if header["eof"] not in (0, 1) or len(entries) != header["entry_count"]:
        raise HarnessError("workspace_manifest_malformed")
    parsed: list[dict[str, object]] = []
    for index in range(1, header["entry_count"] + 1):
        entry = entries.get(index)
        if entry is None or set(entry) != {"object_id", "path", "revision", "size", "kind"}:
            raise HarnessError("workspace_manifest_malformed")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(entry["object_id"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry["revision"]))
            or not re.fullmatch(r"[0-9]+", str(entry["size"]))
        ):
            raise HarnessError("workspace_manifest_malformed")
        entry["size"] = int(str(entry["size"]))
        parsed.append(entry)
    if header["next_cursor"] != header["cursor"] + len(parsed):
        raise HarnessError("workspace_manifest_malformed")
    return header, parsed


def _catalog_semantics(path: str, kind: str) -> tuple[str, str, str]:
    folded = path.casefold()
    suffix = Path(path).suffix.casefold()
    if folded.startswith("user/src/"):
        stage = "application"
    elif folded.startswith("os/"):
        stage = "kernel"
    elif folded.startswith("host_tools/"):
        stage = "host"
    elif folded.startswith("scripts/"):
        stage = "test"
    elif folded.startswith(("include/", "user/include/")):
        stage = "interface"
    elif folded.startswith(("docs/", "agentos-ucore-")):
        stage = "document"
    else:
        stage = "workspace"
    if suffix in {".c", ".s", ".py"}:
        semantic_kind = "source"
    elif suffix in {".h"}:
        semantic_kind = "header"
    elif suffix in {".md", ".tex", ".txt"}:
        semantic_kind = "document"
    elif suffix in {".json", ".jsonl", ".csv"}:
        semantic_kind = "data"
    else:
        semantic_kind = "file" if kind == "file" else "object"
    raw_summary = path.encode("utf-8", errors="replace")
    if len(raw_summary) >= 64:
        raw_summary = raw_summary[:60]
        while raw_summary:
            try:
                summary = raw_summary.decode("utf-8") + "..."
                break
            except UnicodeDecodeError:
                raw_summary = raw_summary[:-1]
        else:
            summary = "workspace object"
    else:
        summary = path
    return stage, semantic_kind, summary or "workspace object"


def _catalog_manifest_prefix(path: str) -> str:
    """Normalize the user-facing relative root without weakening reader checks."""

    normalized = str(PurePosixPath(path))
    return "" if normalized == "." else normalized


def _argument_projection(arguments: Mapping[str, object]) -> dict[str, object]:
    projected: dict[str, object] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value.encode("utf-8")) > 256:
            raw = value.encode("utf-8")
            projected[key] = {"bytes": len(raw), "sha256": _sha256(raw)}
        else:
            projected[key] = value
    return projected


def _result_projection(content: str) -> dict[str, object]:
    projected: dict[str, object] = {}
    allowed = {
        "status",
        "code",
        "path",
        "revision",
        "previous_revision",
        "source_path",
        "source_revision",
        "target",
        "build_id",
        "case_kind",
        "expected_exit",
        "actual_exit",
        "output_match",
        "timed_out",
        "write_id",
        "staged_bytes",
        "atomic_commit",
        "current_revision",
    }
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in allowed:
            projected[key] = value
    return projected


def _workspace_tool_error(
    tool: str,
    operation: str,
    status: str,
    content: str,
    *,
    path: str,
    workspace_generation: str = "",
) -> development.DevelopmentResult:
    """Return one bounded provider rejection that can settle through AgentOS.

    Invalid model-supplied workspace arguments are expected tool outcomes.  The
    controlled Provider therefore returns a sealed error receipt instead of
    tearing down the long-running Agent Loop.  Catalog invariant failures still
    raise HarnessError at their original call sites.
    """

    match = re.search(r"(?:^|\n)workspace_error=([^\n]+)", content)
    code = match.group(1) if match else f"{operation}_{status}"
    body = (
        "workspace_tool_error\n"
        "content_untrusted=1\n"
        "status=rejected\n"
        f"code={_bounded_utf8(code, 96)}\n"
        f"operation={operation}\n"
        f"tool={tool}\n"
        f"path={_bounded_utf8(path, 240)}\n"
        f"detail={_bounded_utf8(content, 320)}"
    )
    return development.DevelopmentResult(
        "error", workspace_generation, body
    )


@dataclass(frozen=True, slots=True)
class AgentConfig:
    capabilities: frozenset[str]
    tools: frozenset[str]
    system_prompt: str
    resource_budget: int = 32
    artifact_count_limit: int = 32
    artifact_bytes_limit: int = 256 * 1024
    artifact_read_limit: int = 1024 * 1024
    summary_high_watermark: int = 24

    def validate(self, policy: "WorkflowPolicy") -> None:
        if not self.capabilities or not self.capabilities <= policy.capabilities:
            raise HarnessError("capability_subset_invalid")
        if not self.tools <= policy.tools or not self.tools <= TOOLS:
            raise HarnessError("tool_subset_invalid")
        if any(TOOL_CAPABILITY[tool] not in self.capabilities for tool in self.tools):
            raise HarnessError("tool_capability_missing")
        if not 1 <= self.resource_budget <= policy.resource_budget:
            raise HarnessError("resource_budget_invalid")
        if not 1 <= self.artifact_count_limit <= policy.artifact_count_limit:
            raise HarnessError("artifact_count_limit_invalid")
        if not 1 <= self.artifact_bytes_limit <= policy.artifact_bytes_limit:
            raise HarnessError("artifact_bytes_limit_invalid")
        if not 1 <= self.artifact_read_limit <= policy.artifact_read_limit:
            raise HarnessError("artifact_read_limit_invalid")
        if not 4 <= self.summary_high_watermark <= 96:
            raise HarnessError("summary_high_watermark_invalid")
        if not self.system_prompt or len(self.system_prompt.encode("utf-8")) > 4096:
            raise HarnessError("system_prompt_invalid")


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    capabilities: frozenset[str]
    tools: frozenset[str]
    resource_budget: int = 64
    artifact_count_limit: int = MAX_ARTIFACTS
    artifact_bytes_limit: int = 2 * 1024 * 1024
    artifact_read_limit: int = 8 * 1024 * 1024


@dataclass(slots=True)
class Artifact:
    handle: int
    kind: str
    content: bytes
    sha256: str
    producer_agent: int
    task_id: int
    source_sequence: int
    lifecycle: tuple[int, int]
    native_context_sequence: int = 0
    native_producer_agent: int = 0
    native_producer_control: int = 0
    sealed: bool = True
    shareable: bool = False
    shared: bool = False
    references: set[tuple[int, int]] = field(default_factory=set)
    retain_until: float = 0.0


class ContextArtifactStore:
    """Bounded user-space body store with kernel-ready seal metadata."""

    def __init__(
        self,
        lifecycle: tuple[int, int],
        maximum: int = MAX_ARTIFACTS,
        maximum_bytes: int = 2 * 1024 * 1024,
        maximum_reads: int = 8 * 1024 * 1024,
    ):
        self.lifecycle = lifecycle
        self.maximum = maximum
        self.maximum_bytes = maximum_bytes
        self.maximum_reads = maximum_reads
        self._next_handle = 1
        self._artifacts: dict[int, Artifact] = {}
        self._agent_bytes: dict[int, int] = {}
        self._agent_reads: dict[int, int] = {}
        self._workflow_bytes = 0
        self._workflow_reads = 0
        self._lock = threading.RLock()

    def reserve(self) -> int:
        """Reserve one handle before native Task admission binds its result."""

        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            return handle

    def put(
        self,
        config: AgentConfig,
        agent_id: int,
        task_id: int,
        source_sequence: int,
        kind: str,
        content: str | bytes,
        *,
        shareable: bool = False,
        retain_seconds: float = 0.0,
        reserved_handle: int = 0,
        native_metadata: Mapping[str, object] | None = None,
    ) -> Artifact:
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        if not raw or len(raw) > MAX_ARTIFACT_BYTES:
            raise HarnessError("artifact_size_invalid")
        if kind not in SHAREABLE_KINDS and shareable:
            raise HarnessError("artifact_kind_not_shareable")
        with self._lock:
            owned_count = sum(
                item.producer_agent == agent_id for item in self._artifacts.values()
            )
            owned_bytes = self._agent_bytes.get(agent_id, 0)
            if (
                len(self._artifacts) >= self.maximum
                or self._workflow_bytes + len(raw) > self.maximum_bytes
                or owned_count >= config.artifact_count_limit
                or owned_bytes + len(raw) > config.artifact_bytes_limit
            ):
                raise HarnessError("artifact_quota_exceeded")
            if reserved_handle:
                if reserved_handle in self._artifacts or not (
                    0 < reserved_handle < self._next_handle
                ):
                    raise HarnessError("artifact_reserved_handle_invalid")
                handle = reserved_handle
            else:
                handle = self._next_handle
                self._next_handle += 1
            artifact = Artifact(
                handle,
                kind,
                raw,
                _sha256(raw),
                agent_id,
                task_id,
                source_sequence,
                self.lifecycle,
                native_context_sequence=(
                    0 if native_metadata is None else
                    int(native_metadata.get("context_sequence", 0))
                ),
                native_producer_agent=(
                    0 if native_metadata is None else
                    int(native_metadata.get("producer_agent_id", 0))
                ),
                native_producer_control=(
                    0 if native_metadata is None else
                    int(native_metadata.get("producer_control_id", 0))
                ),
                shareable=shareable,
                retain_until=time.monotonic() + max(0.0, retain_seconds),
            )
            self._artifacts[handle] = artifact
            self._agent_bytes[agent_id] = owned_bytes + len(raw)
            self._workflow_bytes += len(raw)
            return artifact

    def get(self, handle: int) -> Artifact:
        with self._lock:
            artifact = self._artifacts.get(handle)
            if artifact is None or not artifact.sealed or artifact.lifecycle != self.lifecycle:
                raise HarnessError("artifact_not_found")
            return artifact

    def bind(self, agent_id: int, task_id: int, handle: int) -> Artifact:
        with self._lock:
            artifact = self.get(handle)
            if artifact.producer_agent != agent_id and not artifact.shared:
                raise HarnessError("artifact_read_denied")
            artifact.references.add((agent_id, task_id))
            return artifact

    def share(self, config: AgentConfig, agent_id: int, handle: int) -> Artifact:
        if "SHARE_ARTIFACT" not in config.capabilities:
            raise HarnessError("artifact_share_denied")
        with self._lock:
            artifact = self.get(handle)
            if artifact.producer_agent != agent_id or not artifact.shareable:
                raise HarnessError("artifact_share_denied")
            artifact.shared = True
            return artifact

    def read(
        self, config: AgentConfig, agent_id: int, handle: int, offset: int, length: int
    ) -> bytes:
        if offset < 0 or length <= 0 or length > MAX_MODEL_PROJECTION_BYTES:
            raise HarnessError("artifact_range_invalid")
        with self._lock:
            artifact = self.get(handle)
            if artifact.producer_agent != agent_id and not artifact.shared:
                raise HarnessError("artifact_read_denied")
            selected = artifact.content[offset : offset + length]
            used = self._agent_reads.get(agent_id, 0)
            if (
                used + len(selected) > config.artifact_read_limit
                or self._workflow_reads + len(selected) > self.maximum_reads
            ):
                raise HarnessError("artifact_read_quota")
            self._agent_reads[agent_id] = used + len(selected)
            self._workflow_reads += len(selected)
            return selected

    def release(self, agent_id: int, task_id: int, handle: int) -> None:
        with self._lock:
            artifact = self.get(handle)
            artifact.references.discard((agent_id, task_id))
            if (
                not artifact.references
                and not artifact.shared
                and artifact.retain_until <= time.monotonic()
            ):
                self._agent_bytes[artifact.producer_agent] -= len(artifact.content)
                self._workflow_bytes -= len(artifact.content)
                self._artifacts.pop(handle, None)

    def summary_projection(self, handles: Sequence[int]) -> str:
        rows = []
        with self._lock:
            for handle in handles:
                artifact = self.get(handle)
                rows.append(
                    {
                        "handle": artifact.handle,
                        "kind": artifact.kind,
                        "bytes": len(artifact.content),
                        "sha256": artifact.sha256,
                        "producer_agent": artifact.producer_agent,
                        "task_id": artifact.task_id,
                        "source_sequence": artifact.source_sequence,
                    }
                )
        return _bounded_utf8(_canonical(rows).decode("utf-8"), MAX_MODEL_PROJECTION_BYTES)


@dataclass(frozen=True, slots=True)
class TaskDescriptor:
    task_id: int
    correlation_id: int
    parent_task_id: int
    parent_agent: int
    target_agent: int
    objective_artifact: int
    input_artifact: int
    result_artifact: int
    required_capabilities: frozenset[str]
    allowed_tools: frozenset[str]
    workspace_revision: str
    resource_budget: int
    read_budget: int
    deadline_monotonic: float
    expected_result_kind: str
    operation_tool: str = "delegate_task"


@dataclass(slots=True)
class TaskRecord:
    descriptor: TaskDescriptor
    state: str = "pending"
    result_artifact: int = 0
    terminal_status: str = ""
    claimed_by: int = 0
    model_rounds: int = 0
    tool_calls: int = 0
    native_context_sequence: int = 0


@dataclass(slots=True)
class NativeProvider:
    agent_id: int
    config: AgentConfig
    label: str
    native_pid: int = 0
    native_agent_id: int = 0
    native_control_id: int = 0


class TaskChannel:
    """Host verifier backed by the long-lived Guest's native Task Channel."""

    def __init__(
        self,
        store: ContextArtifactStore,
        native: native_task.NativeTaskChannel | None = None,
    ):
        self.store = store
        self.native = native
        self._tasks: dict[int, TaskRecord] = {}
        self._lock = threading.RLock()

    def publish_root(self, descriptor: TaskDescriptor) -> TaskRecord:
        with self._lock:
            if len(self._tasks) >= MAX_TASKS or descriptor.task_id in self._tasks:
                raise HarnessError("task_capacity_or_reuse")
            if self.native is not None:
                self.native.delegate(descriptor)
            record = TaskRecord(descriptor)
            self._tasks[descriptor.task_id] = record
            return record

    def delegate(
        self,
        parent: "AgentLoop",
        target: "AgentLoop",
        descriptor: TaskDescriptor,
    ) -> TaskRecord:
        with self._lock:
            if len(self._tasks) >= MAX_TASKS or descriptor.task_id in self._tasks:
                raise HarnessError("task_capacity_or_reuse")
            if "ORCHESTRATE" not in parent.config.capabilities:
                raise HarnessError("task_delegate_denied")
            if descriptor.parent_agent != parent.agent_id or descriptor.target_agent != target.agent_id:
                raise HarnessError("task_identity_mismatch")
            if not descriptor.required_capabilities <= parent.config.capabilities:
                raise HarnessError("parent_capability_subset")
            if not descriptor.required_capabilities <= target.config.capabilities:
                raise HarnessError("target_capability_subset")
            if not descriptor.allowed_tools <= parent.config.tools:
                raise HarnessError("parent_tool_subset")
            if not descriptor.allowed_tools <= target.config.tools:
                raise HarnessError("target_tool_subset")
            objective = self.store.get(descriptor.objective_artifact)
            if objective.producer_agent != parent.agent_id or not objective.sealed:
                raise HarnessError("task_objective_invalid")
            if descriptor.input_artifact:
                input_artifact = self.store.get(descriptor.input_artifact)
                if input_artifact.producer_agent != parent.agent_id and not input_artifact.shared:
                    raise HarnessError("task_input_invalid")
            if descriptor.deadline_monotonic <= time.monotonic():
                raise HarnessError("task_deadline_invalid")
            if descriptor.expected_result_kind not in RESULT_KINDS:
                raise HarnessError("task_result_kind_invalid")
            if self.native is not None:
                self.native.delegate(descriptor)
            record = TaskRecord(descriptor)
            self._tasks[descriptor.task_id] = record
            return record

    def publish_brokered(
        self,
        parent: "AgentLoop",
        target: NativeProvider,
        descriptor: TaskDescriptor,
    ) -> TaskRecord:
        """Admit one controlled provider request through the native channel."""

        with self._lock:
            if len(self._tasks) >= MAX_TASKS or descriptor.task_id in self._tasks:
                raise HarnessError("task_capacity_or_reuse")
            if descriptor.parent_agent != parent.agent_id:
                raise HarnessError("task_identity_mismatch")
            if descriptor.target_agent != target.agent_id:
                raise HarnessError("task_identity_mismatch")
            if descriptor.operation_tool not in descriptor.allowed_tools:
                raise HarnessError("task_tool_denied")
            if not descriptor.required_capabilities <= parent.config.capabilities:
                raise HarnessError("parent_capability_subset")
            if not descriptor.required_capabilities <= target.config.capabilities:
                raise HarnessError("target_capability_subset")
            if not descriptor.allowed_tools <= parent.config.tools:
                raise HarnessError("parent_tool_subset")
            if not descriptor.allowed_tools <= target.config.tools:
                raise HarnessError("target_tool_subset")
            artifact = self.store.get(descriptor.input_artifact)
            if (
                artifact.producer_agent != parent.agent_id
                or not artifact.shared
                or artifact.task_id != descriptor.task_id
                or artifact.native_context_sequence <= 0 and self.native is not None
            ):
                raise HarnessError("task_input_invalid")
            if descriptor.result_artifact <= 0:
                raise HarnessError("task_result_handle_invalid")
            if self.native is not None:
                self.native.delegate(descriptor)
                self.native.claim(descriptor.task_id, target.agent_id)
            record = TaskRecord(
                descriptor=descriptor,
                state="claimed",
                claimed_by=target.agent_id,
            )
            self._tasks[descriptor.task_id] = record
            return record

    def claim(self, task_id: int, agent_id: int) -> TaskRecord:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.state != "pending":
                raise HarnessError("task_not_claimable")
            if record.descriptor.target_agent != agent_id:
                raise HarnessError("task_claim_denied")
            if record.descriptor.deadline_monotonic <= time.monotonic():
                record.state = "terminal"
                record.terminal_status = "timeout"
                raise HarnessError("task_timed_out")
            if self.native is not None:
                self.native.claim(task_id, agent_id)
            record.state = "claimed"
            record.claimed_by = agent_id
            return record

    def resume(self, task_id: int, agent_id: int) -> TaskRecord:
        with self._lock:
            record = self._tasks.get(task_id)
            if (
                record is None
                or record.state != "claimed"
                or record.claimed_by != agent_id
            ):
                raise HarnessError("task_not_resumable")
            if record.descriptor.deadline_monotonic <= time.monotonic():
                record.state = "terminal"
                record.terminal_status = "timeout"
                raise HarnessError("task_timed_out")
            return record

    def complete(
        self, task_id: int, agent_id: int, status: str, result_artifact: int
    ) -> TaskRecord:
        with self._lock:
            record = self._tasks.get(task_id)
            if (
                record is None
                or record.state not in {"claimed", "cancelling"}
                or record.claimed_by != agent_id
            ):
                raise HarnessError("task_completion_denied")
            if status == "ok":
                artifact = self.store.get(result_artifact)
                if (
                    artifact.handle != record.descriptor.result_artifact
                    or artifact.producer_agent != agent_id
                    or artifact.task_id != task_id
                    or artifact.kind != record.descriptor.expected_result_kind
                    or not artifact.sealed
                    or (
                        self.native is not None
                        and (
                            artifact.native_context_sequence <= 0
                            or artifact.native_producer_agent <= 0
                            or artifact.native_producer_control <= 0
                        )
                    )
                ):
                    raise HarnessError("task_result_invalid")
            elif result_artifact:
                raise HarnessError("failed_task_has_result")
            if self.native is not None:
                native_result = self.native.complete(
                    task_id, agent_id, status, result_artifact
                )
                record.native_context_sequence = native_result["context_sequence"]
                status = native_task.STATUS_NAMES.get(
                    native_result["status"], f"status_{native_result['status']}"
                )
            record.result_artifact = result_artifact if status == "ok" else 0
            record.terminal_status = status
            record.state = "terminal"
            return record

    def cancel(self, task_id: int, parent_agent: int) -> TaskRecord:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record.descriptor.parent_agent != parent_agent:
                raise HarnessError("task_cancel_denied")
            if record.state == "terminal":
                return record
            if self.native is not None:
                if record.state != "claimed" or record.claimed_by == 0:
                    raise HarnessError("native_task_not_claimed")
                native_result = self.native.cancel(task_id)
                record.native_context_sequence = native_result["context_sequence"]
            record.state = "terminal"
            record.terminal_status = "cancelled"
            return record

    def accept_result(self, task_id: int, parent_agent: int) -> Artifact:
        with self._lock:
            record = self._tasks.get(task_id)
            if (
                record is None
                or record.descriptor.parent_agent != parent_agent
                or record.state != "terminal"
                or record.terminal_status != "ok"
                or record.result_artifact == 0
            ):
                raise HarnessError("task_result_unsettled")
            artifact = self.store.get(record.result_artifact)
            if (
                artifact.producer_agent != record.descriptor.target_agent
                or artifact.task_id != task_id
                or artifact.sha256 != _sha256(artifact.content)
            ):
                raise HarnessError("task_result_evidence_invalid")
            return artifact


Model = Callable[[Mapping[str, object]], Mapping[str, object]]


@dataclass(slots=True)
class AgentLoop:
    harness: "NexusHarness"
    agent_id: int
    config: AgentConfig
    model: Model
    label: str
    native_pid: int = 0
    native_agent_id: int = 0
    native_control_id: int = 0
    private_context: list[dict[str, object]] = field(default_factory=list)
    pending_tasks: deque[int] = field(default_factory=deque)
    sequence: int = 0
    stopped: bool = False
    _condition: threading.Condition = field(default_factory=threading.Condition)
    _thread: threading.Thread | None = None

    def wake(self, task_id: int = 0) -> None:
        with self._condition:
            if task_id:
                self.pending_tasks.append(task_id)
            self._condition.notify_all()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self.run, name=f"nexus-agent-{self.agent_id}", daemon=True
        )
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _append(self, kind: str, **values: object) -> int:
        self.sequence += 1
        row = {"sequence": self.sequence, "kind": kind, **values}
        event = OBSERVABILITY_EVENT.get(kind)
        if event is not None:
            row["event"] = event
        self.private_context.append(row)
        self.harness.trace_event(self.agent_id, row)
        if len(self.private_context) > self.config.summary_high_watermark:
            protected = [
                row for row in self.private_context
                if row.get("kind")
                in (
                    "task_pending",
                    "error",
                    "unmerged_revision",
                    "completion_blocked",
                )
                or (
                    row.get("kind") == "tool_result"
                    and isinstance(row.get("evidence"), Mapping)
                    and (
                        row["evidence"].get("status") == "failed"
                        or "code" in row["evidence"]
                    )
                )
            ]
            history = self.private_context[: -8]
            evidence_rows = [
                row
                for row in history
                if row.get("kind") == "tool_result"
                and isinstance(row.get("evidence"), Mapping)
            ]
            summary_body = {
                "sequence": self.sequence,
                "current_goal": self.harness.goal,
                "completed_work": [
                    row for row in history if row.get("kind") == "task_completed"
                ][-8:],
                "tools": [
                    row.get("tool")
                    for row in history
                    if row.get("kind") == "tool"
                ][-12:],
                "tool_calls": [
                    {
                        "sequence": row.get("sequence"),
                        "task_id": row.get("task_id"),
                        "tool": row.get("tool"),
                    }
                    for row in history
                    if row.get("kind") == "tool"
                ][-12:],
                "modified_files": [
                    {
                        "sequence": row.get("sequence"),
                        "artifact": row.get("artifact"),
                        "sha256": row.get("sha256"),
                        "path": row["evidence"].get("path"),
                        "revision": row["evidence"].get("revision"),
                    }
                    for row in evidence_rows
                    if "revision" in row["evidence"]
                ][-8:],
                "builds_and_tests": [
                    {
                        "sequence": row.get("sequence"),
                        "artifact": row.get("artifact"),
                        "sha256": row.get("sha256"),
                        "evidence": dict(row["evidence"]),
                    }
                    for row in evidence_rows
                    if "build_id" in row["evidence"]
                    or "case_kind" in row["evidence"]
                    or row["evidence"].get("status") == "failed"
                ][-12:],
                "recent_errors": [
                    row for row in history if row.get("kind") == "error"
                ][-8:],
                "unresolved": protected[-8:],
                "next_plan": "continue from the newest retained Context evidence",
                "artifact_handles": [row.get("artifact") for row in history if row.get("artifact")][-16:],
            }
            summary: dict[str, object] = {
                "kind": "summary",
                "event": "SUMMARY_COMMITTED",
                **summary_body,
            }
            try:
                artifact = self.harness.store.put(
                    self.config,
                    self.agent_id,
                    int(values.get("task_id", 0)),
                    self.sequence,
                    "private_summary",
                    _canonical(summary_body),
                )
                summary.update(
                    artifact=artifact.handle,
                    sha256=artifact.sha256,
                    source_sequence=self.sequence,
                )
            except HarnessError:
                summary["summary_store_status"] = "quota_deferred"
            self.private_context = [summary, *protected[-8:], *self.private_context[-8:]]
        return self.sequence

    def _projection(self, task: TaskRecord | None) -> dict[str, object]:
        handles: list[int] = []
        for row in [*self.private_context[-12:], *self.harness.shared_context[-12:]]:
            handle = row.get("artifact")
            if isinstance(handle, int) and handle not in handles:
                handles.append(handle)
        artifact_rows: list[dict[str, object]] = []
        remaining = MAX_MODEL_PROJECTION_BYTES
        for handle in reversed(handles[-8:]):
            if remaining <= 256:
                break
            artifact = self.harness.store.get(handle)
            if artifact.producer_agent != self.agent_id and not artifact.shared:
                continue
            length = min(len(artifact.content), remaining, 2048)
            content = self.harness.store.read(
                self.config, self.agent_id, handle, 0, length
            ).decode("utf-8", errors="replace")
            artifact_rows.append(
                {
                    "handle": handle,
                    "kind": artifact.kind,
                    "bytes": len(artifact.content),
                    "sha256": artifact.sha256,
                    "producer_agent": artifact.producer_agent,
                    "task_id": artifact.task_id,
                    "content": content,
                    "truncated": length < len(artifact.content),
                }
            )
            remaining -= len(content.encode("utf-8"))
        completion_missing = (
            []
            if task is None
            else self.harness._development_completion_missing(
                task.descriptor.task_id
            )
        )
        return {
            "goal": self.harness.goal,
            "agent": {
                "id": self.agent_id,
                "label": self.label,
                "capabilities": sorted(self.config.capabilities),
                "tools": sorted(self.config.tools),
                "system_prompt": self.config.system_prompt,
            },
            "task": None if task is None else {
                "task_id": task.descriptor.task_id,
                "parent_task_id": task.descriptor.parent_task_id,
                "objective": self.harness.store.read(
                    self.config, self.agent_id,
                    task.descriptor.objective_artifact, 0,
                    MAX_MODEL_PROJECTION_BYTES,
                ).decode("utf-8", errors="replace"),
                "allowed_tools": sorted(task.descriptor.allowed_tools),
                "workspace_revision": task.descriptor.workspace_revision,
                "expected_result_kind": task.descriptor.expected_result_kind,
                "input_artifact": (
                    ""
                    if not task.descriptor.input_artifact
                    else self.harness.store.read(
                        self.config,
                        self.agent_id,
                        task.descriptor.input_artifact,
                        0,
                        MAX_MODEL_PROJECTION_BYTES,
                    ).decode("utf-8", errors="replace")
                ),
            },
            "context": self.private_context[-12:],
            "shared_context": self.harness.shared_context[-16:],
            "artifacts": artifact_rows,
            "contract_tool_remaining": self.harness.contract_tool_remaining(),
            "completion_missing": completion_missing,
        }

    def run(self) -> None:
        while not self.stopped and not self.harness.stopped:
            with self._condition:
                if not self.pending_tasks:
                    self._condition.wait(DEFAULT_HEARTBEAT_SECONDS)
                if not self.pending_tasks:
                    self._append("heartbeat")
                    continue
                task_id = self.pending_tasks.popleft()
            try:
                record = self.harness.tasks._tasks.get(task_id)
                if record is None:
                    raise HarnessError("task_not_found")
                if record.state == "pending":
                    task = self.harness.tasks.claim(task_id, self.agent_id)
                    self._append("task_claimed", task_id=task_id)
                else:
                    task = self.harness.tasks.resume(task_id, self.agent_id)
                task.model_rounds += 1
                if task.model_rounds > MAX_ROUNDS:
                    raise HarnessError("task_round_limit")
                model_started = time.monotonic()
                self.harness.events.emit(
                    "model", "model_started",
                    f"Agent {self.agent_id} started model round {task.model_rounds}",
                    agent_id=self.agent_id,
                    task_id=task_id,
                    model_round=task.model_rounds,
                    tool_calls=task.tool_calls,
                )
                provider_model = (
                    self.model if isinstance(self.model, DeepSeekModel) else None
                )
                if provider_model is not None:
                    provider_model.set_progress_context(
                        task_id=task_id, model_round=task.model_rounds
                    )
                try:
                    action = dict(self.model(self._projection(task)))
                except Exception as error:
                    self.harness.events.emit(
                        "model", "model_failed",
                        f"Agent {self.agent_id} model round {task.model_rounds} failed",
                        agent_id=self.agent_id,
                        task_id=task_id,
                        model_round=task.model_rounds,
                        tool_calls=task.tool_calls,
                        error=type(error).__name__,
                        detail=_bounded_utf8(str(error), 160),
                        duration_ms=int((time.monotonic() - model_started) * 1000),
                    )
                    raise
                finally:
                    if provider_model is not None:
                        provider_model.set_progress_context()
                self.harness.events.emit(
                    "model", "model_completed",
                    (
                        f"Agent {self.agent_id} completed model round "
                        f"{task.model_rounds}: {action.get('type', 'invalid')}"
                    ),
                    agent_id=self.agent_id,
                    task_id=task_id,
                    model_round=task.model_rounds,
                    tool_calls=task.tool_calls,
                    action_type=str(action.get("type", "invalid")),
                    selected_tool=str(action.get("tool", "")),
                    duration_ms=int((time.monotonic() - model_started) * 1000),
                )
                self.harness.execute_action(self, task, action)
            except Exception as error:  # fail closed at the Agent boundary
                self._append(
                    "error",
                    task_id=task_id,
                    error=type(error).__name__,
                    detail=_bounded_utf8(str(error), 240),
                )
                try:
                    self.harness.tasks.complete(task_id, self.agent_id, "failed", 0)
                    self._append(
                        "task_completed",
                        task_id=task_id,
                        status="failed",
                        artifact=0,
                    )
                except HarnessError:
                    pass
                self.harness.wake_parent(task_id)
        self.stopped = True


class NexusHarness:
    """Generic Agent runtime, shared Context index, and controlled providers."""

    def __init__(
        self,
        root: Path,
        goal: str,
        policy: WorkflowPolicy,
        *,
        lifecycle: tuple[int, int] = (1, 1),
        model_factory: Callable[[AgentConfig], Model] | None = None,
        trace_path: Path | None = None,
        native_channel: native_task.NativeTaskChannel | None = None,
        event_bus: harness_progress.HarnessEventBus | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        self.goal = goal
        self.policy = policy
        self.native_channel = native_channel
        self.lifecycle = (
            native_channel.lifecycle if native_channel is not None else lifecycle
        )
        self.store = ContextArtifactStore(
            self.lifecycle,
            policy.artifact_count_limit,
            policy.artifact_bytes_limit,
            policy.artifact_read_limit,
        )
        self.tasks = TaskChannel(self.store, native_channel)
        if event_bus is not None and trace_path is not None:
            raise HarnessError("event_bus_trace_conflict")
        self._owns_events = event_bus is None
        self.events = event_bus or harness_progress.HarnessEventBus(
            mode="off", goal=goal, trace_path=trace_path
        )
        self._tool_progress = threading.local()
        self.development = development.NexusDevelopmentBroker(
            self.root, progress_callback=self._development_progress
        )
        self.agents: dict[int, AgentLoop] = {}
        self.providers: dict[str, NativeProvider] = {}
        self.shared_context: list[dict[str, object]] = []
        self._context_lock = threading.RLock()
        self.stopped = False
        self.model_factory = model_factory
        self._next_agent = 1
        self._next_task = 1
        self._next_correlation = 1
        self._lock = threading.RLock()
        self._catalog_lock = threading.RLock()
        self._catalog_invalidated = False

    def contract_tool_remaining(self) -> dict[str, int]:
        used = {name: 0 for name in CONTRACT_TOOL_BUDGET}
        with self.tasks._lock:
            for record in self.tasks._tasks.values():
                operation = record.descriptor.operation_tool
                if operation in used:
                    used[operation] += 1
        return {
            name: max(0, limit - used[name])
            for name, limit in CONTRACT_TOOL_BUDGET.items()
        }

    def _provider_group(self, tool: str) -> tuple[str, frozenset[str], frozenset[str]]:
        if tool in ("search_files", "read_file"):
            allowed = frozenset(("search_files", "read_file")) & self.policy.tools
            if tool not in allowed:
                raise HarnessError("provider_tool_not_allowed")
            return (
                "workspace-read",
                frozenset(("READ_WORKSPACE", "SHARE_ARTIFACT")),
                allowed,
            )
        if tool in ("write_file", "apply_patch"):
            allowed = frozenset(("write_file", "apply_patch")) & self.policy.tools
            if tool not in allowed:
                raise HarnessError("provider_tool_not_allowed")
            return (
                "workspace-mutation",
                frozenset(("WRITE_WORKSPACE", "SHARE_ARTIFACT")),
                allowed,
            )
        if tool in ("build_ucore_program", "run_ucore_program"):
            allowed = frozenset(
                ("build_ucore_program", "run_ucore_program")
            ) & self.policy.tools
            if tool not in allowed:
                raise HarnessError("provider_tool_not_allowed")
            return (
                "execution",
                frozenset(("BUILD", "RUN", "SHARE_ARTIFACT")),
                allowed,
            )
        raise HarnessError("unknown_tool")

    def _provider_for(self, tool: str) -> NativeProvider:
        group, capabilities, tools = self._provider_group(tool)
        with self._lock:
            existing = self.providers.get(group)
            if existing is not None:
                return existing
            if len(self.agents) + len(self.providers) >= MAX_AGENTS:
                raise HarnessError("agent_capacity")
            config = AgentConfig(
                capabilities=capabilities,
                tools=tools,
                system_prompt="Execute only claimed controlled provider requests.",
                resource_budget=min(32, self.policy.resource_budget),
                artifact_count_limit=min(32, self.policy.artifact_count_limit),
                artifact_bytes_limit=min(
                    512 * 1024, self.policy.artifact_bytes_limit
                ),
                artifact_read_limit=min(
                    1024 * 1024, self.policy.artifact_read_limit
                ),
                summary_high_watermark=24,
            )
            config.validate(self.policy)
            agent_id = self._next_agent
            self._next_agent += 1
            provider = NativeProvider(agent_id, config, f"{group}-provider")
            if self.native_channel is not None:
                identity = self.native_channel.spawn(
                    agent_id, config, channel_owner=False
                )
                provider.native_pid = identity["pid"]
                provider.native_agent_id = identity["agent_id"]
                provider.native_control_id = identity["control_id"]
            self.providers[group] = provider
            self.events.emit(
                "agent", "provider_spawned",
                f"Controlled {group} Provider is ready",
                agent_id=agent_id, provider_group=group,
                capabilities=sorted(capabilities), tools=sorted(tools),
                native_pid=provider.native_pid,
                native_agent_id=provider.native_agent_id,
                native_control_id=provider.native_control_id,
            )
            return provider

    def trace_event(self, agent_id: int, row: Mapping[str, object]) -> None:
        self.events.emit_context(agent_id, row)

    def _development_progress(
        self, kind: str, fields: Mapping[str, object]
    ) -> None:
        source = "build" if kind.startswith("build_") else "run"
        context = getattr(self._tool_progress, "context", {})
        merged = {**dict(context), **dict(fields)}
        messages = {
            "build_worktree_started": "Creating an isolated build worktree",
            "build_worktree_completed": "Isolated build worktree is ready",
            "build_worktree_failed": "Isolated build worktree creation failed",
            "build_command_started": (
                f"Running build command {merged.get('command_index', 0)}/"
                f"{merged.get('command_count', 0)}"
            ),
            "build_command_completed": (
                f"Build command {merged.get('command_index', 0)}/"
                f"{merged.get('command_count', 0)} exited with "
                f"{merged.get('exit_status', 'unknown')}"
            ),
            "build_completed": "uCore program build completed",
            "build_failed": "uCore program build failed",
            "run_guest_started": (
                f"Starting isolated Guest for {merged.get('case_kind', 'test')} case"
            ),
            "run_input_sent": (
                f"Sent bounded input for {merged.get('case_kind', 'test')} case"
            ),
            "run_guest_completed": (
                f"Isolated Guest {merged.get('case_kind', 'test')} case completed"
            ),
        }
        self.events.emit(
            source, kind, messages.get(kind, kind.replace("_", " ")), **merged
        )

    def close(self) -> None:
        self.stopped = True
        for agent in self.agents.values():
            agent.wake()
        deadline = time.monotonic() + (
            development.BUILD_TIMEOUT_SECONDS
            + development.RUN_TIMEOUT_SECONDS
            + 20
        )
        for agent in self.agents.values():
            agent.join(max(0.0, deadline - time.monotonic()))
        if any(agent.is_alive() for agent in self.agents.values()):
            raise HarnessError("agent_shutdown_timeout")
        self.development.close()
        if self.native_channel is not None:
            self.native_channel.close()
        if self._owns_events:
            self.events.close()

    def spawn(
        self, config: AgentConfig, model: Model | None = None, label: str = "agent"
    ) -> AgentLoop:
        config.validate(self.policy)
        if model is None:
            if self.model_factory is None:
                raise HarnessError("model_factory_missing")
            model = self.model_factory(config)
        with self._lock:
            if len(self.agents) + len(self.providers) >= MAX_AGENTS:
                raise HarnessError("agent_capacity")
            agent_id = self._next_agent
            self._next_agent += 1
            if isinstance(model, DeepSeekModel):
                model.bind_progress(
                    lambda kind, message, fields, bound_agent=agent_id: (
                        self.events.emit(
                            "model", kind, message,
                            agent_id=bound_agent, **dict(fields)
                        )
                    )
                )
            agent = AgentLoop(self, agent_id, config, model, label)
            if self.native_channel is not None:
                identity = self.native_channel.spawn(agent_id, config)
                agent.native_pid = identity["pid"]
                agent.native_agent_id = identity["agent_id"]
                agent.native_control_id = identity["control_id"]
            self.agents[agent_id] = agent
            agent.start()
            self.events.emit(
                "agent", "agent_spawned",
                f"Agent {agent_id} ({label}) is running",
                agent_id=agent_id,
                label=label,
                native_pid=agent.native_pid,
                native_agent_id=agent.native_agent_id,
                native_control_id=agent.native_control_id,
            )
            return agent

    def submit_root(
        self,
        agent: AgentLoop,
        objective: str | None = None,
        *,
        expected_result_kind: str = "final",
        deadline_seconds: float = 900.0,
    ) -> int:
        """Publish one user objective without inventing a privileged Agent role."""
        with self._lock:
            task_id = self._next_task
            self._next_task += 1
        sequence = agent._append("user", task_id=task_id)
        artifact = self.store.put(
            agent.config,
            agent.agent_id,
            task_id,
            sequence,
            "user",
            objective or self.goal,
            shareable=True,
        )
        self.store.share(agent.config, agent.agent_id, artifact.handle)
        result_handle = self.store.reserve()
        correlation_id = self._next_correlation
        self._next_correlation += 1
        descriptor = TaskDescriptor(
            task_id=task_id,
            correlation_id=correlation_id,
            parent_task_id=0,
            parent_agent=agent.agent_id,
            target_agent=agent.agent_id,
            objective_artifact=artifact.handle,
            input_artifact=0,
            result_artifact=result_handle,
            required_capabilities=agent.config.capabilities,
            allowed_tools=agent.config.tools,
            workspace_revision="",
            resource_budget=agent.config.resource_budget,
            read_budget=agent.config.artifact_read_limit,
            deadline_monotonic=time.monotonic() + deadline_seconds,
            expected_result_kind=expected_result_kind,
            operation_tool="delegate_task",
        )
        self.tasks.publish_root(descriptor)
        self.events.emit(
            "task", "root_task_submitted",
            f"Root Task {task_id} submitted",
            task_id=task_id,
            parent_task_id=0,
            parent_agent=agent.agent_id,
            target_agent=agent.agent_id,
            resource_budget=descriptor.resource_budget,
        )
        agent.wake(task_id)
        return task_id

    def delegate(
        self,
        parent: AgentLoop,
        target: AgentLoop,
        objective: str,
        *,
        parent_task_id: int,
        input_artifact: int = 0,
        required_capabilities: frozenset[str] = frozenset(),
        allowed_tools: frozenset[str] = frozenset(),
        workspace_revision: str = "",
        resource_budget: int = 16,
        read_budget: int = 64 * 1024,
        deadline_seconds: float = 300.0,
        expected_result_kind: str = "subtask_report",
    ) -> int:
        task_id = self._next_task
        self._next_task += 1
        sequence = parent._append("task_pending", task_id=task_id)
        objective_artifact = self.store.put(
            parent.config, parent.agent_id, task_id, sequence, "user",
            objective, shareable=True,
        )
        self.store.share(parent.config, parent.agent_id, objective_artifact.handle)
        result_handle = self.store.reserve()
        correlation_id = self._next_correlation
        self._next_correlation += 1
        descriptor = TaskDescriptor(
            task_id,
            correlation_id,
            parent_task_id,
            parent.agent_id,
            target.agent_id,
            objective_artifact.handle,
            input_artifact,
            result_handle,
            required_capabilities,
            allowed_tools,
            workspace_revision,
            resource_budget,
            read_budget,
            time.monotonic() + deadline_seconds,
            expected_result_kind,
            "delegate_task",
        )
        self.tasks.delegate(parent, target, descriptor)
        self.events.emit(
            "task", "task_delegated",
            f"Task {task_id} delegated from Agent {parent.agent_id} to Agent {target.agent_id}",
            task_id=task_id,
            parent_task_id=parent_task_id,
            parent_agent=parent.agent_id,
            target_agent=target.agent_id,
            resource_budget=resource_budget,
        )
        target.wake(task_id)
        return task_id

    def wake_parent(self, task_id: int) -> None:
        record = self.tasks._tasks.get(task_id)
        if record is None or record.descriptor.parent_task_id == 0:
            return
        parent = self.agents.get(record.descriptor.parent_agent)
        if parent is not None:
            parent._append(
                "task_completed",
                task_id=task_id,
                status=record.terminal_status,
                artifact=record.result_artifact,
            )
            if record.terminal_status == "ok":
                artifact = self.tasks.accept_result(task_id, parent.agent_id)
                parent._append(
                    "artifact_accepted",
                    task_id=task_id,
                    producer_agent=artifact.producer_agent,
                    artifact=artifact.handle,
                    sha256=artifact.sha256,
                    source_sequence=artifact.source_sequence,
                )
            if record.descriptor.parent_task_id:
                parent.wake(record.descriptor.parent_task_id)

    def _commit_workflow_summary(
        self, producer: AgentLoop, task_id: int, result: Artifact
    ) -> None:
        with self.tasks._lock:
            task_items = tuple(sorted(self.tasks._tasks.items()))
        settled = {
            current_id
            for current_id, record in task_items
            if record.state == "terminal" and record.terminal_status == "ok"
        }
        with self.store._lock:
            artifact_items = tuple(
                sorted(self.store._artifacts.values(), key=lambda item: item.handle)
            )
        evidence_rows: list[dict[str, object]] = []
        for current in self.agents.values():
            for row in current.private_context:
                if (
                    row.get("kind") == "tool_result"
                    and row.get("task_id") in settled
                    and isinstance(row.get("evidence"), Mapping)
                ):
                    evidence_rows.append(row)
        body = {
            "source_sequence": producer.sequence,
            "task_graph": [
                {
                    "task_id": current_id,
                    "parent_task_id": record.descriptor.parent_task_id,
                    "parent_agent": record.descriptor.parent_agent,
                    "target_agent": record.descriptor.target_agent,
                    "state": record.state,
                    "status": record.terminal_status,
                }
                for current_id, record in task_items
            ],
            "agent_assignments": [
                {
                    "agent_id": current.agent_id,
                    "capabilities": sorted(current.config.capabilities),
                    "tools": sorted(current.config.tools),
                }
                for current in sorted(
                    self.agents.values(), key=lambda item: item.agent_id
                )
            ],
            "public_artifacts": [
                {
                    "handle": artifact.handle,
                    "task_id": artifact.task_id,
                    "producer_agent": artifact.producer_agent,
                    "sha256": artifact.sha256,
                    "source_sequence": artifact.source_sequence,
                }
                for artifact in artifact_items
                if artifact.shared and artifact.task_id in settled
            ][-24:],
            "file_revisions": [
                {
                    "path": row["evidence"].get("path"),
                    "revision": row["evidence"].get("revision"),
                    "artifact": row.get("artifact"),
                    "sha256": row.get("sha256"),
                }
                for row in evidence_rows
                if "revision" in row["evidence"]
            ][-16:],
            "builds_and_tests": [
                {
                    "artifact": row.get("artifact"),
                    "sha256": row.get("sha256"),
                    "evidence": dict(row["evidence"]),
                }
                for row in evidence_rows
                if "build_id" in row["evidence"]
                or "case_kind" in row["evidence"]
            ][-16:],
            "conflicts": [
                dict(row["evidence"])
                for row in evidence_rows
                if row["evidence"].get("code") == "revision_conflict"
            ][-8:],
            "unfinished_tasks": [
                current_id
                for current_id, record in task_items
                if record.state != "terminal"
            ],
            "accepted_result": {
                "handle": result.handle,
                "task_id": task_id,
                "producer_agent": result.producer_agent,
                "sha256": result.sha256,
            },
        }
        summary_row: dict[str, object] = {
            "kind": "workflow_summary",
            "event": "SUMMARY_COMMITTED",
            **body,
        }
        try:
            summary = self.store.put(
                producer.config,
                producer.agent_id,
                task_id,
                producer.sequence,
                "team_summary",
                _canonical(body),
                shareable=True,
            )
            self.store.share(producer.config, producer.agent_id, summary.handle)
            summary_row.update(
                artifact=summary.handle,
                sha256=summary.sha256,
            )
        except HarnessError:
            summary_row["summary_store_status"] = "quota_deferred"
        with self._context_lock:
            self.shared_context = [
                row
                for row in self.shared_context
                if row.get("kind") != "workflow_summary"
            ][-15:]
            self.shared_context.append(summary_row)
        self.trace_event(producer.agent_id, summary_row)

    def _development_completion_missing(self, root_task_id: int) -> list[str]:
        root = self.tasks._tasks[root_task_id]
        required_tools = root.descriptor.allowed_tools
        if not {"build_ucore_program", "run_ucore_program"} <= required_tools:
            return []

        def belongs(task_id: int) -> bool:
            seen: set[int] = set()
            while task_id and task_id not in seen:
                if task_id == root_task_id:
                    return True
                seen.add(task_id)
                record = self.tasks._tasks.get(task_id)
                if record is None:
                    return False
                task_id = record.descriptor.parent_task_id
            return False

        builds: dict[str, str] = {}
        cases: dict[str, set[str]] = {}
        observed_workspace_read = False
        observed_workspace_mutation = False
        latest_source_revision = ""
        with self.store._lock:
            artifacts = tuple(self.store._artifacts.values())
        for artifact in artifacts:
            if not artifact.sealed or not artifact.shared or not belongs(artifact.task_id):
                continue
            try:
                text = artifact.content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            fields = {}
            lines = text.splitlines()
            result_kind = lines[0] if lines else ""
            for line in lines:
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields.setdefault(key, value)
            record = self.tasks._tasks.get(artifact.task_id)
            tool = "" if record is None else record.descriptor.operation_tool
            if (
                tool in {"search_files", "read_file"}
                and result_kind == "catalog_evidence_v1"
                and fields.get("used_index") == "1"
                and fields.get("workspace_generation")
                and int(fields.get("catalog_records", "0"))
                > int(fields.get("catalog_candidates", "0"))
            ):
                observed_workspace_read = True
            if (
                tool == "write_file"
                and result_kind == "workspace_write"
                and fields.get("revision")
                and fields.get("atomic_commit") == "1"
            ) or (
                tool == "apply_patch"
                and result_kind == "workspace_patch"
                and fields.get("revision")
                and fields.get("atomic_commit") == "1"
            ):
                observed_workspace_mutation = True
                latest_source_revision = fields.get("revision", "")
            build_id = fields.get("build_id", "")
            if (
                artifact.kind == "build_diagnostic"
                and fields.get("status") == "passed"
                and build_id
            ):
                builds[build_id] = fields.get("source_revision", "")
            if (
                artifact.kind == "test_result"
                and fields.get("status") == "passed"
                and build_id
            ):
                observed = {
                    value for value in fields.get("case_kinds", "").split(",")
                    if value in development.CASE_KINDS
                }
                if fields.get("case_kind") in development.CASE_KINDS:
                    observed.add(fields["case_kind"])
                cases.setdefault(build_id, set()).update(observed)
        missing = []
        if (
            required_tools & {"search_files", "read_file"}
            and not observed_workspace_read
        ):
            missing.append("workspace_read")
        if (
            required_tools & {"write_file", "apply_patch"}
            and not observed_workspace_mutation
        ):
            missing.append("workspace_mutation")
        current_builds = {
            build_id
            for build_id, source_revision in builds.items()
            if not latest_source_revision or source_revision == latest_source_revision
        }
        if not current_builds:
            missing.append("successful_build")
        completed_build = next(
            (
                build_id for build_id in current_builds
                if development.CASE_KINDS <= cases.get(build_id, set())
            ),
            "",
        )
        if not completed_build:
            observed_cases = set().union(*(cases.values())) if cases else set()
            missing.extend(sorted(development.CASE_KINDS - observed_cases))
        return missing

    @staticmethod
    def _artifact_kind_for_tool(tool: str) -> str:
        return {
            "write_file": "file",
            "apply_patch": "patch",
            "build_ucore_program": "build_diagnostic",
            "run_ucore_program": "test_result",
            "search_files": "search",
            "read_file": "file",
        }[tool]

    def _next_task_identity(self) -> tuple[int, int]:
        with self._lock:
            task_id = self._next_task
            self._next_task += 1
            correlation_id = self._next_correlation
            self._next_correlation += 1
        return task_id, correlation_id

    def _execute_brokered_tool(
        self,
        agent: AgentLoop,
        parent_task: TaskRecord,
        tool: str,
        arguments: Mapping[str, object],
    ) -> tuple[development.DevelopmentResult, Artifact, TaskRecord, int]:
        """Run one Host operation only after native Task admission and claim."""

        if self.contract_tool_remaining().get(tool, 0) <= 0:
            raise HarnessError("contract_tool_budget")
        provider = self._provider_for(tool)
        task_id, correlation_id = self._next_task_identity()
        sequence = agent._append(
            "tool",
            task_id=task_id,
            parent_task_id=parent_task.descriptor.task_id,
            correlation_id=correlation_id,
            tool=tool,
            arguments=_argument_projection(arguments),
        )
        request_body = _canonical(
            {
                "version": 1,
                "task_id": task_id,
                "parent_task_id": parent_task.descriptor.task_id,
                "correlation_id": correlation_id,
                "tool": tool,
                "arguments": dict(arguments),
            }
        )
        input_handle = self.store.reserve()
        native_input: Mapping[str, object] | None = None
        if self.native_channel is not None:
            sealed_input = self.native_channel.seal_artifact(
                host_agent_id=agent.agent_id,
                task_id=task_id,
                handle=input_handle,
                kind="tool",
                tool=tool,
                content=request_body,
                host_context_sequence=sequence,
                shareable=True,
            )
            native_input = self.native_channel.bind_artifact(
                host_agent_id=agent.agent_id,
                task_id=task_id,
                handle=input_handle,
                kind="tool",
                tool=tool,
                length=len(request_body),
                sha256=str(sealed_input["sha256"]),
                host_context_sequence=sequence,
                cause_sequence=int(sealed_input["context_sequence"]),
            )
        input_artifact = self.store.put(
            agent.config,
            agent.agent_id,
            task_id,
            sequence,
            "tool",
            request_body,
            shareable=True,
            reserved_handle=input_handle,
            native_metadata=native_input,
        )
        self.store.share(agent.config, agent.agent_id, input_handle)
        result_handle = self.store.reserve()
        artifact_kind = self._artifact_kind_for_tool(tool)
        workspace_revision = str(
            arguments.get("expected_revision")
            or arguments.get("source_revision")
            or arguments.get("build_id")
            or ""
        )
        descriptor = TaskDescriptor(
            task_id=task_id,
            correlation_id=correlation_id,
            parent_task_id=parent_task.descriptor.task_id,
            parent_agent=agent.agent_id,
            target_agent=provider.agent_id,
            objective_artifact=input_artifact.handle,
            input_artifact=input_artifact.handle,
            result_artifact=result_handle,
            required_capabilities=frozenset((TOOL_CAPABILITY[tool],)),
            allowed_tools=frozenset((tool,)),
            workspace_revision=workspace_revision,
            resource_budget=1,
            read_budget=min(64 * 1024, provider.config.artifact_read_limit),
            deadline_monotonic=min(
                parent_task.descriptor.deadline_monotonic,
                time.monotonic()
                + development.BUILD_TIMEOUT_SECONDS
                + development.RUN_TIMEOUT_SECONDS
                + 30.0,
            ),
            expected_result_kind=artifact_kind,
            operation_tool=tool,
        )
        record = self.tasks.publish_brokered(agent, provider, descriptor)
        self.events.emit(
            "task", "tool_task_claimed",
            f"Controlled Provider claimed {tool} Task {task_id}",
            task_id=task_id,
            parent_task_id=parent_task.descriptor.task_id,
            parent_agent=agent.agent_id,
            target_agent=provider.agent_id,
            correlation_id=correlation_id,
            tool=tool,
            input_artifact=input_handle,
            result_artifact=result_handle,
        )
        try:
            result = self.call_tool(
                tool,
                arguments,
                provider=provider,
                task_id=task_id,
            )
            result_body = result.content.encode("utf-8")
            native_result: Mapping[str, object] | None = None
            if self.native_channel is not None:
                native_result = self.native_channel.seal_artifact(
                    host_agent_id=provider.agent_id,
                    task_id=task_id,
                    handle=result_handle,
                    kind=artifact_kind,
                    tool=tool,
                    content=result_body,
                    host_context_sequence=sequence,
                    shareable=True,
                )
            artifact = self.store.put(
                provider.config,
                provider.agent_id,
                task_id,
                sequence,
                artifact_kind,
                result_body,
                shareable=True,
                reserved_handle=result_handle,
                native_metadata=native_result,
            )
            self.store.share(provider.config, provider.agent_id, result_handle)
            record = self.tasks.complete(
                task_id, provider.agent_id, "ok", result_handle
            )
        except Exception:
            try:
                if record.state == "claimed":
                    self.tasks.complete(task_id, provider.agent_id, "failed", 0)
            finally:
                raise
        merge_sequence = 0
        if self.native_channel is not None:
            bound = self.native_channel.bind_artifact(
                host_agent_id=agent.agent_id,
                task_id=task_id,
                handle=result_handle,
                kind=artifact_kind,
                tool=tool,
                length=len(artifact.content),
                sha256=artifact.sha256,
                host_context_sequence=sequence,
                cause_sequence=record.native_context_sequence,
            )
            merge_sequence = int(bound["context_sequence"])
        accepted = self.tasks.accept_result(task_id, agent.agent_id)
        if accepted.handle != artifact.handle:
            raise HarnessError("task_result_evidence_invalid")
        return result, artifact, record, merge_sequence

    def execute_action(
        self, agent: AgentLoop, task: TaskRecord, action: Mapping[str, object]
    ) -> None:
        kind = action.get("type")
        if kind == "delegate":
            if self.contract_tool_remaining()["delegate_task"] <= 0:
                raise HarnessError("contract_delegate_budget")
            if "ORCHESTRATE" not in agent.config.capabilities:
                raise HarnessError("task_delegate_denied")
            objective = action.get("objective")
            capabilities = action.get("capabilities")
            tools = action.get("tools")
            if (
                not isinstance(objective, str)
                or not objective
                or not isinstance(capabilities, list)
                or not all(isinstance(value, str) for value in capabilities)
                or not isinstance(tools, list)
                or not all(isinstance(value, str) for value in tools)
            ):
                raise HarnessError("task_delegate_invalid")
            child_capabilities = frozenset(capabilities)
            child_tools = frozenset(tools)
            if (
                not child_capabilities <= task.descriptor.required_capabilities
                or not child_tools <= task.descriptor.allowed_tools
            ):
                raise HarnessError("task_authority_subset")
            prompt = action.get("system_prompt", "Execute the assigned task and return evidence.")
            if not isinstance(prompt, str):
                raise HarnessError("task_prompt_invalid")
            config = AgentConfig(
                capabilities=child_capabilities,
                tools=child_tools,
                system_prompt=prompt,
                resource_budget=min(
                    int(action.get("resource_budget", task.descriptor.resource_budget)),
                    task.descriptor.resource_budget,
                ),
                artifact_count_limit=min(32, agent.config.artifact_count_limit),
                artifact_bytes_limit=min(256 * 1024, agent.config.artifact_bytes_limit),
                artifact_read_limit=min(
                    int(action.get("read_budget", task.descriptor.read_budget)),
                    task.descriptor.read_budget,
                ),
            )
            requested_target = action.get("target_agent", 0)
            target = self.agents.get(requested_target) if isinstance(requested_target, int) else None
            if target is not None:
                if (
                    not child_capabilities <= target.config.capabilities
                    or not child_tools <= target.config.tools
                ):
                    raise HarnessError("target_authority_subset")
            else:
                target = self.spawn(config, label=str(action.get("label", "agent")))
            child_task = self.delegate(
                agent,
                target,
                objective,
                parent_task_id=task.descriptor.task_id,
                input_artifact=int(action.get("input_artifact", 0)),
                required_capabilities=child_capabilities,
                allowed_tools=child_tools,
                workspace_revision=str(action.get("workspace_revision", "")),
                resource_budget=config.resource_budget,
                read_budget=config.artifact_read_limit,
                deadline_seconds=float(action.get("deadline_seconds", 300.0)),
                expected_result_kind=str(action.get("expected_result_kind", "subtask_report")),
            )
            agent._append(
                "task_delegated",
                task_id=child_task,
                parent_task_id=task.descriptor.task_id,
                target_agent=target.agent_id,
                capabilities=sorted(child_capabilities),
                tools=sorted(child_tools),
            )
            if bool(action.get("continue_parent", False)):
                agent.wake(task.descriptor.task_id)
            return
        if kind == "wait":
            outstanding = [
                record.descriptor.task_id
                for record in self.tasks._tasks.values()
                if record.descriptor.parent_agent == agent.agent_id
                and record.descriptor.parent_task_id == task.descriptor.task_id
                and record.state != "terminal"
            ]
            agent._append(
                "task_wait",
                task_id=task.descriptor.task_id,
                outstanding_tasks=outstanding,
            )
            if not outstanding:
                agent.wake(task.descriptor.task_id)
            return
        if kind == "tool":
            tool = action.get("tool")
            arguments = action.get("arguments")
            if not isinstance(tool, str) or tool not in task.descriptor.allowed_tools:
                raise HarnessError("task_tool_denied")
            if tool not in agent.config.tools or not isinstance(arguments, dict):
                raise HarnessError("agent_tool_denied")
            task.tool_calls += 1
            if task.tool_calls > task.descriptor.resource_budget:
                raise HarnessError("task_resource_budget")
            projected_arguments = _argument_projection(arguments)
            tool_started = time.monotonic()
            progress_context = {
                "agent_id": agent.agent_id,
                "task_id": task.descriptor.task_id,
                "tool": tool,
                "model_round": task.model_rounds,
                "tool_calls": task.tool_calls,
            }
            self.events.emit(
                "tool", "tool_started",
                f"Agent {agent.agent_id} started {tool}",
                **progress_context,
                arguments=projected_arguments,
            )
            self._tool_progress.context = progress_context
            try:
                result, artifact, tool_task, merge_sequence = (
                    self._execute_brokered_tool(agent, task, tool, arguments)
                )
            except Exception as error:
                self.events.emit(
                    "tool", "tool_failed",
                    f"Tool {tool} raised {type(error).__name__}",
                    **progress_context,
                    detail=_bounded_utf8(str(error), 160),
                    duration_ms=int((time.monotonic() - tool_started) * 1000),
                )
                raise
            finally:
                self._tool_progress.context = {}
            evidence = _result_projection(result.content)
            self.events.emit(
                "tool", "tool_completed",
                f"Agent {agent.agent_id} completed {tool}",
                **progress_context,
                status=result.status,
                workspace_revision=result.workspace_generation,
                evidence=evidence,
                duration_ms=int((time.monotonic() - tool_started) * 1000),
            )
            result_row = {
                "sequence": agent.sequence,
                "kind": "tool_result",
                "event": "ARTIFACT_SHARED",
                "task_id": tool_task.descriptor.task_id,
                "parent_task_id": task.descriptor.task_id,
                "correlation_id": tool_task.descriptor.correlation_id,
                "tool": tool,
                "artifact": artifact.handle,
                "sha256": artifact.sha256,
                "native_artifact_sequence": artifact.native_context_sequence,
                "native_terminal_sequence": tool_task.native_context_sequence,
                "native_merge_sequence": merge_sequence,
                "workspace_revision": result.workspace_generation,
                "evidence": evidence,
            }
            agent.private_context.append(result_row)
            self.trace_event(agent.agent_id, result_row)
            self.events.emit(
                "harness", "artifact_sealed",
                f"Artifact {artifact.handle} sealed for Task {tool_task.descriptor.task_id}",
                agent_id=agent.agent_id,
                task_id=tool_task.descriptor.task_id,
                parent_task_id=task.descriptor.task_id,
                correlation_id=tool_task.descriptor.correlation_id,
                artifact=artifact.handle,
                artifact_kind=artifact.kind,
                bytes=len(artifact.content),
                sha256=artifact.sha256,
                artifact_count=len(self.store._artifacts),
            )
            # The same event-driven loop resumes after the sealed result exists.
            agent.wake(task.descriptor.task_id)
            return
        if kind == "final":
            content = action.get("content")
            if not isinstance(content, str) or not content:
                raise HarnessError("final_invalid")
            if task.descriptor.expected_result_kind not in RESULT_KINDS:
                raise HarnessError("final_kind_invalid")
            if any(
                child.descriptor.parent_agent == agent.agent_id
                and child.descriptor.parent_task_id == task.descriptor.task_id
                and child.state != "terminal"
                for child in self.tasks._tasks.values()
            ):
                raise HarnessError("child_task_unsettled")
            completion_missing = self._development_completion_missing(
                task.descriptor.task_id
            )
            if completion_missing:
                agent._append(
                    "completion_blocked",
                    task_id=task.descriptor.task_id,
                    missing=completion_missing,
                )
                agent.wake(task.descriptor.task_id)
                return
            sequence = agent._append("final", task_id=task.descriptor.task_id)
            native_final: Mapping[str, object] | None = None
            final_body = content.encode("utf-8")
            if self.native_channel is not None:
                native_final = self.native_channel.seal_artifact(
                    host_agent_id=agent.agent_id,
                    task_id=task.descriptor.task_id,
                    handle=task.descriptor.result_artifact,
                    kind=task.descriptor.expected_result_kind,
                    tool="delegate_task",
                    content=final_body,
                    host_context_sequence=sequence,
                    shareable=True,
                )
            artifact = self.store.put(
                agent.config,
                agent.agent_id,
                task.descriptor.task_id,
                sequence,
                task.descriptor.expected_result_kind,
                final_body,
                shareable=True,
                reserved_handle=task.descriptor.result_artifact,
                native_metadata=native_final,
            )
            self.store.share(agent.config, agent.agent_id, artifact.handle)
            self.events.emit(
                "harness", "artifact_sealed",
                f"Final Artifact {artifact.handle} sealed",
                agent_id=agent.agent_id,
                task_id=task.descriptor.task_id,
                artifact=artifact.handle,
                artifact_kind=artifact.kind,
                bytes=len(artifact.content),
                sha256=artifact.sha256,
                artifact_count=len(self.store._artifacts),
            )
            self.tasks.complete(task.descriptor.task_id, agent.agent_id, "ok", artifact.handle)
            agent._append(
                "task_completed",
                task_id=task.descriptor.task_id,
                status="ok",
                artifact=artifact.handle,
            )
            self._commit_workflow_summary(
                agent, task.descriptor.task_id, artifact
            )
            self.wake_parent(task.descriptor.task_id)
            return
        raise HarnessError("model_action_invalid")

    def _workspace_catalog_tool(
        self,
        tool: str,
        arguments: Mapping[str, object],
        provider: NativeProvider,
        task_id: int,
    ) -> development.DevelopmentResult:
        if self.native_channel is None:
            raise HarnessError("catalog_native_guest_required")
        requested_path = str(arguments.get("path", ""))
        prefix = _catalog_manifest_prefix(requested_path)
        query_text = str(arguments.get("query", ""))
        requested_stage = str(arguments.get("stage", ""))
        requested_kind = str(arguments.get("kind", ""))
        requested_status = str(arguments.get("status", "current"))
        summary_contains = str(arguments.get("summary_contains", ""))
        if (
            tool == "search_files"
            and prefix == ""
            and query_text in ("", ".")
            and not requested_stage
            and not requested_kind
            and not summary_contains
        ):
            return _workspace_tool_error(
                tool,
                "search",
                "error",
                "workspace_error=query_too_broad",
                path=requested_path,
            )
        if prefix == "":
            prefix = {
                "application": "user/src",
                "kernel": "os",
                "host": "host_tools",
                "test": "scripts",
            }.get(requested_stage, prefix)
        if tool == "read_file":
            requested = PurePosixPath(requested_path)
            prefix = "" if str(requested.parent) == "." else str(requested.parent)
            summary_contains = requested.name
        cursor = 0
        generation = ""
        host_body_reads = 0
        catalog_records = 0
        catalog_candidates = 0
        watch_events = 0
        reused_pages = 0
        result_sections: list[str] = []
        selected_read: tuple[Mapping[str, object], str] | None = None
        reader = workspace.WorkspaceReader(self.root)
        try:
            with self._catalog_lock:
                if self._catalog_invalidated:
                    self.native_channel.catalog_stale(
                        host_agent_id=provider.agent_id, task_id=task_id
                    )
                    self._catalog_invalidated = False
                while True:
                    manifest = reader.manifest(
                        generation, cursor, 8, prefix
                    )
                    if manifest.status != "ok":
                        return _workspace_tool_error(
                            tool,
                            "manifest",
                            manifest.status,
                            manifest.content,
                            path=requested_path,
                            workspace_generation=manifest.workspace_generation,
                        )
                    if not generation:
                        generation = manifest.workspace_generation
                    elif manifest.workspace_generation != generation:
                        raise HarnessError("workspace_manifest_generation_changed")
                    header, entries = _parse_workspace_manifest(manifest.content)
                    if not entries:
                        break
                    enriched: list[dict[str, object]] = []
                    for entry in entries:
                        stage, semantic_kind, summary = _catalog_semantics(
                            str(entry["path"]), str(entry["kind"])
                        )
                        enriched.append(
                            {
                                **entry,
                                "stage": stage,
                                "kind": semantic_kind,
                                "summary": summary,
                            }
                        )
                    loaded = self.native_channel.catalog_load(
                        host_agent_id=provider.agent_id,
                        task_id=task_id,
                        workspace_generation=generation,
                        cursor=cursor,
                        eof=bool(header["eof"]),
                        entries=enriched,
                    )
                    reused_pages += int(loaded["reuse"])
                    watch_events = max(watch_events, int(loaded["watch_events"]))
                    selected = self.native_channel.catalog_query(
                        host_agent_id=provider.agent_id,
                        task_id=task_id,
                        stage=requested_stage,
                        kind=requested_kind,
                        status=requested_status,
                        summary_contains=summary_contains,
                    )
                    mask = int(selected["candidate_mask"])
                    identities = selected["identities"]
                    page_candidates: list[Mapping[str, object]] = []
                    for index, entry in enumerate(enriched):
                        if not mask & (1 << index):
                            continue
                        identity = identities[index]
                        if min(
                            int(identity["dev"]),
                            int(identity["inum"]),
                            int(identity["incarnation"]),
                        ) <= 0:
                            raise HarnessError("catalog_candidate_identity_invalid")
                        page_candidates.append(entry)
                    if len(page_candidates) != int(selected["candidates"]):
                        raise HarnessError("catalog_candidate_mask_mismatch")
                    if int(selected["records"]) < len(page_candidates):
                        raise HarnessError("catalog_candidate_count_invalid")
                    catalog_records += int(selected["records"])
                    catalog_candidates += len(page_candidates)
                    watch_events = max(watch_events, int(selected["watch_events"]))
                    if tool == "search_files" and page_candidates:
                        candidates = [
                            {
                                "object_id": entry["object_id"],
                                "path": entry["path"],
                                "revision": entry["revision"],
                            }
                            for entry in page_candidates
                        ]
                        searched = reader.search_candidates(
                            generation, query_text, candidates
                        )
                        if searched.status != "ok":
                            return _workspace_tool_error(
                                tool,
                                "search",
                                searched.status,
                                searched.content,
                                path=requested_path,
                                workspace_generation=searched.workspace_generation,
                            )
                        host_body_reads += len(candidates)
                        result_sections.append(searched.content)
                    elif tool == "read_file":
                        for entry in page_candidates:
                            if str(entry["path"]) == requested_path:
                                selected_read = (entry, generation)
                                break
                    if selected_read is not None or header["eof"]:
                        break
                    cursor = header["next_cursor"]
            if tool == "read_file":
                if selected_read is None:
                    content = "workspace_read\nstatus=not_found"
                else:
                    entry, selected_generation = selected_read
                    read = reader.read_versioned(
                        selected_generation,
                        str(entry["object_id"]),
                        str(entry["path"]),
                        str(entry["revision"]),
                        int(arguments.get("start_line", 1)),
                        int(arguments.get("max_lines", 64)),
                    )
                    if read.status != "ok":
                        return _workspace_tool_error(
                            tool,
                            "read",
                            read.status,
                            read.content,
                            path=requested_path,
                            workspace_generation=read.workspace_generation,
                        )
                    host_body_reads = 1
                    content = (
                        f"workspace_revision={entry['revision']}\n{read.content}"
                    )
            else:
                content = "\n\n".join(result_sections) or (
                    "workspace_search\nmatch_count=0"
                )
            evidence = (
                "catalog_evidence_v1\n"
                f"workspace_generation={generation}\n"
                "used_index=1\n"
                f"catalog_records={catalog_records}\n"
                f"catalog_candidates={catalog_candidates}\n"
                f"host_body_reads={host_body_reads}\n"
                f"watch_events={watch_events}\n"
                f"reused_pages={reused_pages}\n"
            )
            if host_body_reads > catalog_candidates:
                raise HarnessError("catalog_host_read_exceeded_candidates")
            return development.DevelopmentResult(
                "ok", generation, _bounded_utf8(evidence + content, 12_000)
            )
        finally:
            reader.close()

    def call_tool(
        self,
        tool: str,
        arguments: Mapping[str, object],
        *,
        provider: NativeProvider | None = None,
        task_id: int = 0,
    ) -> development.DevelopmentResult:
        if tool == "write_file":
            result = self.development.write_file_chunk(
                arguments.get("path"), arguments.get("content"),
                arguments.get("expected_revision"),
                arguments.get("write_id"), arguments.get("commit"),
            )
            if result.status == "ok" and arguments.get("commit") == 1:
                self._catalog_invalidated = True
            return result
        if tool == "apply_patch":
            result = self.development.apply_patch(
                arguments.get("path"), arguments.get("patch"),
                arguments.get("expected_revision"),
            )
            if result.status == "ok":
                self._catalog_invalidated = True
            return result
        if tool == "build_ucore_program":
            return self.development.build_ucore_program(
                arguments.get("source_path"), arguments.get("source_revision"),
                arguments.get("target")
            )
        if tool == "run_ucore_program":
            return self.development.run_ucore_program(
                arguments.get("build_id"), arguments.get("cases")
            )
        if tool in ("search_files", "read_file"):
            if provider is None or task_id <= 0:
                raise HarnessError("catalog_task_context_missing")
            return self._workspace_catalog_tool(tool, arguments, provider, task_id)
        raise HarnessError("unknown_tool")


def default_policy() -> WorkflowPolicy:
    return WorkflowPolicy(CAPABILITIES, TOOLS)


def _object_schema(
    properties: Mapping[str, object], required: Sequence[str]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


TOOL_SCHEMAS: Final = {
    "search_files": _object_schema(
        {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "stage": {"type": "string", "maxLength": 15},
            "kind": {"type": "string", "maxLength": 15},
            "status": {"type": "string", "maxLength": 15},
            "summary_contains": {"type": "string", "maxLength": 63},
        },
        ("query", "path"),
    ),
    "read_file": _object_schema(
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        ("path", "start_line", "max_lines"),
    ),
    "write_file": _object_schema(
        {
            "path": {"type": "string"},
            "content": {
                "type": "string",
                "maxLength": development.MAX_WRITE_CHUNK_BYTES,
            },
            "expected_revision": {"type": "string"},
            "write_id": {"type": "string", "maxLength": 64},
            "commit": {"type": "integer", "enum": [0, 1]},
        },
        ("path", "content", "expected_revision", "write_id", "commit"),
    ),
    "apply_patch": _object_schema(
        {
            "path": {"type": "string"},
            "patch": {
                "type": "string",
                "maxLength": relay.MAX_NEXUS_TOOL_ARGUMENT_STRING_BYTES,
            },
            "expected_revision": {"type": "string"},
        },
        ("path", "patch", "expected_revision"),
    ),
    "build_ucore_program": _object_schema(
        {
            "source_path": {"type": "string"},
            "source_revision": {"type": "string"},
            "target": {"type": "string"},
        },
        ("source_path", "source_revision", "target"),
    ),
    "run_ucore_program": _object_schema(
        {
            "build_id": {"type": "string"},
            "cases": {
                "type": "array",
                "minItems": 1,
                "maxItems": development.MAX_RUN_CASES,
                "items": _object_schema(
                    {
                        "name": {"type": "string"},
                        "stdin": {"type": "string"},
                        "expected_output": {"type": "string"},
                        "expected_exit": {"type": "integer"},
                        "case_kind": {
                            "type": "string",
                            "enum": ["normal", "invalid", "failure"],
                        },
                    },
                    ("name", "stdin", "expected_output", "expected_exit", "case_kind"),
                ),
            },
        },
        ("build_id", "cases"),
    ),
}


DELEGATE_SCHEMA: Final = _object_schema(
    {
        "objective": {"type": "string"},
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {"type": "string"}},
        "system_prompt": {"type": "string"},
        "label": {"type": "string"},
        "input_artifact": {"type": "integer"},
        "workspace_revision": {"type": "string"},
        "resource_budget": {"type": "integer"},
        "read_budget": {"type": "integer"},
        "deadline_seconds": {"type": "number"},
        "expected_result_kind": {"type": "string"},
        "continue_parent": {"type": "boolean"},
    },
    ("objective", "capabilities", "tools", "system_prompt", "label"),
)

WAIT_SCHEMA: Final = _object_schema({}, ())
COMPLETE_SCHEMA: Final = _object_schema(
    {"content": {"type": "string", "minLength": 1}}, ("content",)
)

TOOL_DESCRIPTIONS: Final = {
    "search_files": (
        "Search bounded UTF-8 files below the configured workspace root through "
        "the Guest Metadata Catalog. Use stage, kind, status, or summary_contains "
        "to select a smaller candidate set before Host content matching. Typical "
        "stage values are application, kernel, host, test, interface, and document; "
        "typical kind values are source, header, document, and data. Paths must be "
        "workspace-relative; use '.' for the workspace root and never use '/'. For "
        "user-program development, start with path='user/src' and a meaningful query. "
        "An unfiltered query='.' at the workspace root is rejected as too broad."
    ),
    "read_file": (
        "Read a bounded line range from one relative UTF-8 workspace file after "
        "the Guest Catalog validates its directory candidate and revision. "
        "max_lines must be between 1 and 64. Absolute paths, link escapes, and "
        "oversized output are rejected."
    ),
    "write_file": (
        "Atomically create or replace user/src/nexus_<name>_ucore.c. Supply "
        "expected_revision='missing' for a new file, or the exact 64-hex revision "
        "returned by the preceding write or patch. content is limited to 8000 "
        "characters per call. For a larger file, begin with write_id='' and "
        "commit=0, continue with the returned write_id, then set commit=1 on the "
        "last chunk. Staged chunks remain invisible until the final atomic commit."
    ),
    "apply_patch": (
        "Apply one unified diff atomically to user/src/nexus_<name>_ucore.c. "
        "The patch headers must name that exact path and expected_revision must "
        "match the latest write or patch Artifact."
    ),
    "build_ucore_program": (
        "Compile an allowed Nexus user source in an isolated temporary worktree "
        "with the fixed RISC-V toolchain. source_revision must match the current "
        "file revision and the target must equal the source stem. "
        "A successful result returns build_id and source_revision."
    ),
    "run_ucore_program": (
        "Run one to six structured cases for an existing build_id. Every case starts "
        "a separate AgentOS-uCore Guest, receives bounded serial input, and records "
        "actual output, exit status, timeout state, and a Guest log digest."
    ),
}


class DeepSeekModel:
    """Adapter from the common Agent projection to the bounded relay provider."""

    _counter = 0
    _counter_lock = threading.Lock()
    _ACTION_REPAIR_INSTRUCTION = (
        "This Agent Loop accepts exactly one next action per round. Return either "
        "one advertised tool call or one final answer. Do not batch, parallelize, "
        "or emit a plan as multiple tool calls; perform later actions in later rounds."
    )

    def __init__(
        self, provider: relay.ModelProvider, config: AgentConfig, model: str
    ):
        self.provider = provider
        self.config = config
        self.model = model
        self._progress_callback: (
            Callable[[str, str, Mapping[str, object]], None] | None
        ) = None
        self._progress_context: dict[str, object] = {}

    def bind_progress(
        self,
        callback: Callable[[str, str, Mapping[str, object]], None],
    ) -> None:
        self._progress_callback = callback

    def set_progress_context(self, **fields: object) -> None:
        self._progress_context = dict(fields)

    def _progress(self, kind: str, message: str, **fields: object) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(
                kind, message, {**self._progress_context, **fields}
            )
        except Exception:
            # Provider observability cannot affect the bounded Agent action.
            pass

    @classmethod
    def _correlation(cls) -> int:
        with cls._counter_lock:
            cls._counter += 1
            return cls._counter

    def _complete_with_retry(
        self, request: Mapping[str, object], *, attempts: int = 3
    ) -> relay.ModelReply:
        last_error: relay.ProviderError | None = None
        for attempt in range(attempts):
            request_started = time.monotonic()
            correlation = int(request.get("corr_id", 0))
            self._progress(
                "model_request_started",
                f"Provider request {correlation} attempt {attempt + 1} started",
                correlation_id=correlation,
                provider_attempt=attempt + 1,
                provider_attempts=attempts,
            )
            try:
                reply = self.provider.complete(
                    request, deadline_monotonic=time.monotonic() + 120.0
                )
            except relay.ProviderError as error:
                will_retry = (
                    error.code not in (
                        "MULTIPLE_TOOL_CALLS",
                        "TOOL_ARGUMENT_SCHEMA_MISMATCH",
                        "INCOMPLETE_MODEL_RESPONSE",
                        "TOOL_NOT_ADVERTISED",
                    )
                    and error.retryable
                    and attempt + 1 < attempts
                )
                self._progress(
                    "model_request_failed",
                    f"Provider request {correlation} failed with {error.code}",
                    correlation_id=correlation,
                    provider_attempt=attempt + 1,
                    provider_attempts=attempts,
                    error_code=error.code,
                    retrying=int(will_retry),
                    duration_ms=int(
                        (time.monotonic() - request_started) * 1000
                    ),
                )
                if error.code in (
                    "MULTIPLE_TOOL_CALLS",
                    "TOOL_ARGUMENT_SCHEMA_MISMATCH",
                    "INCOMPLETE_MODEL_RESPONSE",
                    "TOOL_NOT_ADVERTISED",
                ) or not error.retryable:
                    raise
                last_error = error
                if attempt + 1 < attempts:
                    delay = 0.5 * (attempt + 1)
                    self._progress(
                        "model_request_retrying",
                        f"Retrying provider request {correlation}",
                        correlation_id=correlation,
                        next_provider_attempt=attempt + 2,
                        retry_delay_ms=int(delay * 1000),
                    )
                    time.sleep(delay)
            else:
                self._progress(
                    "model_request_completed",
                    f"Provider request {correlation} completed",
                    correlation_id=correlation,
                    provider_attempt=attempt + 1,
                    provider_attempts=attempts,
                    reply_type=reply.type,
                    duration_ms=int(
                        (time.monotonic() - request_started) * 1000
                    ),
                )
                return reply
        assert last_error is not None
        raise last_error

    def _select_and_force_one_action(
        self,
        request: Mapping[str, object],
        tools: Sequence[Mapping[str, object]],
        deadline: float,
    ) -> relay.ModelReply:
        names = tuple(str(tool["name"]) for tool in tools)
        final_allowed = "complete_task" in names
        selectable = (*names, "final") if final_allowed else names
        selector = dict(request)
        selector["corr_id"] = self._correlation()
        selector["tools"] = [
            {
                "name": "select_next_action",
                "description": (
                    "Select exactly one admissible next Agent Loop action. This is "
                    "a protocol repair step; it does not execute the selected action."
                ),
                "input_schema": _object_schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": list(selectable),
                        }
                    },
                    ("action",),
                ),
            }
        ]
        selector["tool_choice"] = {"tool": "select_next_action"}
        selector["max_tokens"] = 128
        selector["system"] = (
            str(request["system"])
            + "\n\nThe previous response did not produce one admissible bounded "
            "action. Select only the "
            "single next action. Return exactly one action name with no punctuation "
            "or explanation. Allowed names: "
            + ", ".join(selectable)
            + "."
        )
        selection = self._complete_with_retry(selector)
        if (
            selection.type != "tool_use"
            or selection.tool != "select_next_action"
            or not isinstance(selection.arguments, Mapping)
        ):
            raise HarnessError("provider_action_selection_invalid")
        selected = selection.arguments.get("action")
        if selected == "final" and final_allowed:
            final_request = dict(request)
            final_request["corr_id"] = self._correlation()
            final_tool = "provide_final_answer"
            final_request["tools"] = [
                {
                    "name": final_tool,
                    "description": (
                        "Provide the bounded final report for this Agent task."
                    ),
                    "input_schema": _object_schema(
                        {
                            "content": {
                                "type": "string",
                                "maxLength": 2048,
                            }
                        },
                        ("content",),
                    ),
                }
            ]
            final_request["tool_choice"] = {"tool": final_tool}
            final_request["system"] = (
                str(request["system"])
                + "\n\nProvide one concise final report of at most 2048 characters "
                "through the required protocol tool."
            )
            final_reply = self._complete_with_retry(final_request)
            if (
                final_reply.type != "tool_use"
                or final_reply.tool != final_tool
                or not isinstance(final_reply.arguments, Mapping)
                or not isinstance(final_reply.arguments.get("content"), str)
            ):
                raise HarnessError("provider_final_arguments_invalid")
            return relay.ModelReply(
                "final", content=str(final_reply.arguments["content"])
            )
        if selected not in names:
            raise HarnessError("provider_action_selection_invalid")
        forced = dict(request)
        forced["corr_id"] = self._correlation()
        selected_contract = next(
            tool for tool in tools if tool["name"] == selected
        )
        # Keep the real tool name in the repair request.  DeepSeek reliably obeys
        # exact tool choice for the advertised product tool, while an artificial
        # wrapper name can make it emit the semantically selected tool and fail the
        # protocol even though its arguments are otherwise valid.
        forced["tools"] = [dict(selected_contract)]
        forced["tool_choice"] = {"tool": selected}
        forced["system"] = (
            str(request["system"])
            + "\n\n"
            + self._ACTION_REPAIR_INSTRUCTION
            + f" Call {selected} now."
        )
        if selected == "write_file":
            forced["system"] = (
                str(forced["system"])
                + " The content field must stay below 8000 UTF-8 bytes. If the "
                "complete file is longer, send only its first contiguous chunk "
                "with write_id='' and commit=0; continue from the returned write_id "
                "in later Agent Loop rounds. Do not include the remaining source now."
            )
        try:
            repaired = self._complete_with_retry(forced)
        except relay.ProviderError as error:
            if error.code not in (
                "TOOL_CHOICE_MISMATCH",
                "TOOL_ARGUMENT_SCHEMA_MISMATCH",
            ):
                raise
            # Some DeepSeek endpoints ignore exact tool choice after a long
            # context even when they selected the same action one request
            # earlier.  Recover only the arguments as a bounded JSON object,
            # then apply the original schema before the action can reach the
            # native Task Channel.  This does not execute a Host-side shortcut.
            argument_request = dict(request)
            argument_request["corr_id"] = self._correlation()
            argument_request["tools"] = []
            argument_request["tool_choice"] = "none"
            argument_request["max_tokens"] = 2048
            argument_request["system"] = (
                str(request["system"])
                + "\n\nThe next action has already been selected as "
                + selected
                + ". Return only one JSON object containing its arguments. Do not "
                "use Markdown, prose, or a tool call. The object must satisfy this "
                "JSON Schema: "
                + json.dumps(
                    selected_contract["input_schema"],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            argument_reply = self._complete_with_retry(argument_request)
            if argument_reply.type != "final":
                raise HarnessError("provider_action_arguments_invalid")
            encoded = argument_reply.content.strip()
            if encoded.startswith("```"):
                encoded = re.sub(r"^```(?:json)?\s*", "", encoded, count=1)
                encoded = re.sub(r"\s*```$", "", encoded, count=1)
            try:
                arguments = json.loads(encoded)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HarnessError("provider_action_arguments_invalid") from exc
            if not isinstance(arguments, Mapping) or not relay._json_value_matches_schema(
                arguments, selected_contract["input_schema"]
            ):
                raise HarnessError("provider_action_arguments_invalid")
            return relay.ModelReply(
                "tool_use", tool=selected, arguments=dict(arguments)
            )
        if repaired.type != "tool_use" or repaired.tool != selected:
            raise HarnessError("provider_action_arguments_invalid")
        return repaired

    def __call__(self, projection: Mapping[str, object]) -> Mapping[str, object]:
        advertised_names = set(self.config.tools)
        remaining = projection.get("contract_tool_remaining", {})
        if isinstance(remaining, Mapping):
            for name in tuple(advertised_names):
                if remaining.get(name) == 0:
                    advertised_names.discard(name)
        context_rows = projection.get("context", [])
        if not isinstance(context_rows, list):
            context_rows = []
        failed_build_revision = ""
        failed_build_index = -1
        successful_build_available = False
        successful_build_index = -1
        successful_build_revision = ""
        workspace_mutation_available = False
        for index, row in enumerate(context_rows):
            if not isinstance(row, Mapping) or row.get("kind") != "tool_result":
                continue
            evidence = row.get("evidence")
            if (
                row.get("tool") in {"write_file", "apply_patch"}
                and isinstance(evidence, Mapping)
                and isinstance(evidence.get("revision"), str)
                and evidence.get("revision")
            ):
                workspace_mutation_available = True
            if (
                row.get("tool") == "build_ucore_program"
                and isinstance(evidence, Mapping)
                and evidence.get("status") == "passed"
                and isinstance(evidence.get("build_id"), str)
                and evidence.get("build_id")
            ):
                successful_build_available = True
                successful_build_index = index
                successful_build_revision = str(evidence.get("source_revision", ""))
            if (
                isinstance(evidence, Mapping)
                and evidence.get("status") == "failed"
                and isinstance(evidence.get("source_revision"), str)
            ):
                failed_build_revision = str(evidence["source_revision"])
                failed_build_index = index
        if failed_build_index >= 0:
            changed = False
            for row in context_rows[failed_build_index + 1 :]:
                if not isinstance(row, Mapping):
                    continue
                evidence = row.get("evidence")
                revision = evidence.get("revision") if isinstance(evidence, Mapping) else None
                if isinstance(revision, str) and revision != failed_build_revision:
                    changed = True
                    break
            if not changed:
                advertised_names.discard("build_ucore_program")
        if not workspace_mutation_available:
            advertised_names.discard("build_ucore_program")
            advertised_names.discard("run_ucore_program")
        if successful_build_index >= 0:
            source_changed = False
            for row in context_rows[successful_build_index + 1 :]:
                if not isinstance(row, Mapping) or row.get("tool") not in {
                    "write_file", "apply_patch"
                }:
                    continue
                evidence = row.get("evidence")
                revision = (
                    evidence.get("revision") if isinstance(evidence, Mapping) else None
                )
                if (
                    isinstance(revision, str)
                    and revision
                    and revision != successful_build_revision
                ):
                    source_changed = True
                    break
            if not source_changed:
                advertised_names.discard("build_ucore_program")
            else:
                # A build id is immutable evidence for one exact source
                # revision.  Once the Agent commits another revision it must
                # rebuild before run_ucore_program can be offered again.
                advertised_names.discard("run_ucore_program")
            advertised_names.discard("search_files")
        if not successful_build_available:
            advertised_names.discard("run_ucore_program")
        tools = [
            {
                "name": name,
                "description": TOOL_DESCRIPTIONS[name],
                "input_schema": TOOL_SCHEMAS[name],
            }
            for name in sorted(advertised_names)
        ]
        delegate_remaining = (
            remaining.get("delegate_task")
            if isinstance(remaining, Mapping) else None
        )
        if "ORCHESTRATE" in self.config.capabilities and delegate_remaining != 0:
            tools.append(
                {
                    "name": "delegate_task",
                    "description": (
                        "Create a dynamic child task. Grant only the capabilities and "
                        "tools required by the objective. Set continue_parent=true "
                        "when independent child tasks should run concurrently."
                    ),
                    "input_schema": DELEGATE_SCHEMA,
                }
            )
            tools.append(
                {
                    "name": "wait_for_tasks",
                    "description": (
                        "Suspend this Agent Loop until its outstanding child tasks "
                        "reach terminal states and publish their sealed Artifacts."
                    ),
                    "input_schema": WAIT_SCHEMA,
                }
            )
        completion_missing = projection.get("completion_missing", [])
        if not completion_missing:
            tools.append(
                {
                    "name": "complete_task",
                    "description": (
                        "Submit this Agent Task's final report. Development completion is "
                        "accepted only after indexed workspace evidence, one atomic source "
                        "mutation, a controlled build, and independent Guest test Artifacts "
                        "satisfy the objective."
                    ),
                    "input_schema": COMPLETE_SCHEMA,
                }
            )
        system = (
            self.config.system_prompt
            + "\n\nYou are one instance of the common Nexus Agent Loop. Decide the next "
            "step from the objective, private Context, settled shared Context, and "
            "sealed Artifact projections. Use delegate_task only when parallel or "
            "specialized work is useful. Before reporting software development as "
            "complete, first inspect the workspace through a selective Metadata "
            "Catalog query whose candidate count is smaller than the Catalog record "
            "count, then require an atomic source mutation, a successful controlled "
            "build, and real Guest evidence "
            "for normal, invalid, and important failure cases. Never assume a tool "
            "succeeded without its Artifact evidence. The private Context records "
            "prior tool arguments: do not repeat an identical read or search. For a "
            "single workflow the enforced Contract permits at most three search_files "
            "calls and three read_file calls, so reuse retained Artifact content and "
            "move to implementation once the required interfaces are known. For a "
            "development task, use the evidence in Context to decide when to create or "
            "modify the program, build it, and run the required cases. Source written "
            "for a software-development objective must implement that objective; do "
            "not substitute an unrelated audit, probe, or API-visibility program. New programs "
            "must use a new "
            "path matching user/src/nexus_<name>_ucore.c; never replace an existing "
            "application unless the objective explicitly names it. A similar existing "
            "program is reference material only: submit one revision-checked write_file "
            "or apply_patch for this development task before requesting a build. Keep the program "
            "concise; use staged write_file chunks only when it cannot fit one call. "
            "After a failed build, modify the exact source_path and revision named "
            "by that diagnostic before building again. If apply_patch reports "
            "patch_invalid, replace the full file through revision-checked write_file. "
            "AgentOS-uCore user programs may call only declarations present under "
            "user/include; hosted-C helpers such as exit, fgets, fflush, and strcspn "
            "are unavailable unless those headers declare them. The controlled build "
            "does not provide a user.h umbrella header: include the specific supported "
            "headers such as <stdio.h>, <stdlib.h>, <string.h>, and <unistd.h>. "
            "Treat the leading diagnostic_summary section of a failed build Artifact "
            "as the authoritative compiler excerpt before editing the exact revision. "
            "The controlled build "
            "runner invokes the program as int main(void), so do not require argc or "
            "argv; receive test input through read(0, ...) and write results through "
            "printf or write. Read a bounded line incrementally until newline instead "
            "of waiting for a large fixed-size read. Keep automatic local arrays and "
            "the total user stack comfortably below 3072 bytes; prefer a small "
            "streaming parser over large token stacks. Use the kernel user interfaces "
            "and return an exit status when appropriate."
            " A test case passes only when expected_output is a substring of the "
            "actual serial output and expected_exit equals the program's real return "
            "status. Prefer a consistent nonzero return such as 1 for ordinary input "
            "validation failures, and describe each expected message exactly."
            " All workspace paths are relative to the configured root. Use '.' when "
            "searching from the root; absolute paths and parent traversal are rejected "
            "with a structured tool Artifact that should be corrected next round."
        )
        request = {
            "corr_id": 0,
            "model": self.model,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": _bounded_utf8(
                        _model_projection_json(projection), MAX_MODEL_PROJECTION_BYTES
                    ),
                }
            ],
            "tools": tools,
            "tool_choice": "required",
            "max_tokens": 4096,
        }
        request["corr_id"] = self._correlation()
        try:
            reply = self._complete_with_retry(request)
        except relay.ProviderError as error:
            oversized_final = (
                error.code == "BAD_PROVIDER_RESPONSE"
                and "final content exceeds" in error.public_message
            )
            if (
                error.code
                not in (
                    "MULTIPLE_TOOL_CALLS",
                    "TOOL_ARGUMENT_SCHEMA_MISMATCH",
                    "INCOMPLETE_MODEL_RESPONSE",
                    "TOOL_NOT_ADVERTISED",
                )
                and not oversized_final
                or (
                    not oversized_final
                    and
                    error.code != "INCOMPLETE_MODEL_RESPONSE"
                    and not error.retryable
                )
            ):
                raise
            reply = self._select_and_force_one_action(
                request, tools, time.monotonic() + 120.0
            )
        if reply.type == "tool_use":
            if reply.tool == "delegate_task":
                return {"type": "delegate", **dict(reply.arguments or {})}
            if reply.tool == "wait_for_tasks":
                return {"type": "wait"}
            if reply.tool == "complete_task":
                return {
                    "type": "final",
                    "content": str((reply.arguments or {}).get("content", "")),
                }
            return {
                "type": "tool",
                "tool": reply.tool,
                "arguments": dict(reply.arguments or {}),
            }
        if reply.type == "final":
            return {"type": "final", "content": reply.content}
        raise HarnessError("provider_reply_invalid")


class DeepSeekModelFactory:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = relay.DEEPSEEK_DEFAULT_ENDPOINT,
        model: str = relay.DEEPSEEK_DEFAULT_MODEL,
    ) -> None:
        if not api_key:
            raise HarnessError("api_key_missing")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model

    def __call__(self, config: AgentConfig) -> Model:
        client = relay.JsonHttpsClient(
            self.endpoint,
            timeout_seconds=120.0,
            max_response_bytes=relay.DEFAULT_MAX_HTTP_RESPONSE_BYTES,
            secrets_to_redact=(self.api_key,),
        )
        provider = relay.DeepSeekProvider(
            client,
            api_key=self.api_key,
            model=self.model,
            serialize_auto_tool_calls=True,
        )
        return DeepSeekModel(provider, config, self.model)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--endpoint", default=relay.DEEPSEEK_DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=relay.DEEPSEEK_DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument(
        "--progress", choices=harness_progress.PROGRESS_MODES, default="off"
    )
    parser.add_argument("--status-interval", type=float, default=1.0)
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--native-boot-timeout", type=float, default=150.0)
    return parser.parse_args()


def main() -> int:
    args = cli()
    policy = default_policy()
    root_document: Mapping[str, object] = {}
    if args.config:
        document = json.loads(args.config.read_text(encoding="utf-8"))
        policy = WorkflowPolicy(
            frozenset(document["capabilities"]),
            frozenset(document["tools"]),
            int(document.get("resource_budget", 64)),
            int(document.get("artifact_count_limit", MAX_ARTIFACTS)),
            int(document.get("artifact_bytes_limit", 2 * 1024 * 1024)),
            int(document.get("artifact_read_limit", 8 * 1024 * 1024)),
        )
        candidate = document.get("root_agent", {})
        if not isinstance(candidate, Mapping):
            raise HarnessError("root_agent_config_invalid")
        root_document = candidate
    if args.api_key_file:
        api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    else:
        api_key = os.environ.get(args.api_key_env, "").strip()
    factory = DeepSeekModelFactory(
        api_key, endpoint=args.endpoint, model=args.model
    )
    root_config = AgentConfig(
        capabilities=frozenset(root_document.get("capabilities", policy.capabilities)),
        tools=frozenset(root_document.get("tools", policy.tools)),
        system_prompt=str(
            root_document.get(
                "system_prompt",
                "Plan and execute the user objective with the least authority required.",
            )
        ),
        resource_budget=int(root_document.get("resource_budget", policy.resource_budget)),
        artifact_count_limit=int(
            root_document.get("artifact_count_limit", min(64, policy.artifact_count_limit))
        ),
        artifact_bytes_limit=int(
            root_document.get("artifact_bytes_limit", min(1024 * 1024, policy.artifact_bytes_limit))
        ),
        artifact_read_limit=int(
            root_document.get("artifact_read_limit", min(4 * 1024 * 1024, policy.artifact_read_limit))
        ),
        summary_high_watermark=int(root_document.get("summary_high_watermark", 24)),
    )
    events = harness_progress.HarnessEventBus(
        mode=args.progress, goal=args.goal, trace_path=args.trace_file
    )
    native_channel: native_task.NativeTaskChannel | None = None
    with_harness: NexusHarness | None = None
    monitor: KernelStatusMonitor | None = None
    try:
        events.emit(
            "harness", "workflow_starting", "Starting Nexus Harness workflow",
            model=args.model,
            timeout_seconds=args.timeout,
            workspace=str(args.workspace.resolve()),
        )
        native_channel = native_task.NativeTaskChannel(
            qemu=args.qemu,
            kernel=args.kernel,
            image=args.image,
            boot_timeout=args.native_boot_timeout,
            event_callback=lambda source, kind, message, fields: events.emit(
                source, kind, message, **dict(fields)
            ),
        )
        with_harness = NexusHarness(
            args.workspace,
            args.goal,
            policy,
            model_factory=factory,
            native_channel=native_channel,
            event_bus=events,
        )
        if args.progress != "off" or args.trace_file is not None:
            monitor = KernelStatusMonitor(
                native_channel, events, args.status_interval
            )
            monitor.start()
        root_agent = with_harness.spawn(root_config, label="configured-agent")
        root_task = with_harness.submit_root(
            root_agent, deadline_seconds=args.timeout
        )
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            record = with_harness.tasks._tasks[root_task]
            if record.state == "terminal":
                fence_receipt = native_channel.fence(root_task)
                events.emit(
                    "harness", "workflow_completed",
                    f"Workflow reached terminal status {record.terminal_status}",
                    task_id=root_task,
                    status=record.terminal_status,
                    agents=len(with_harness.agents),
                )
                result = {
                    "status": record.terminal_status,
                    "task_id": root_task,
                    "agents": len(with_harness.agents),
                    "shared_context": with_harness.shared_context,
                    "task_records": [
                        {
                            "task_id": item.descriptor.task_id,
                            "parent_task_id": item.descriptor.parent_task_id,
                            "parent_agent": item.descriptor.parent_agent,
                            "target_agent": item.descriptor.target_agent,
                            "state": item.state,
                            "status": item.terminal_status,
                            "result_artifact": item.result_artifact,
                            "model_rounds": item.model_rounds,
                            "tool_calls": item.tool_calls,
                        }
                        for item in with_harness.tasks._tasks.values()
                    ],
                    "agent_contexts": {
                        str(item.agent_id): item.private_context[-24:]
                        for item in with_harness.agents.values()
                    },
                    "native_guest": {
                        "lifecycle": list(with_harness.lifecycle),
                        "fence": fence_receipt,
                        "agents": {
                            str(item.agent_id): {
                                "pid": item.native_pid,
                                "agent_id": item.native_agent_id,
                                "control_id": item.native_control_id,
                            }
                            for item in with_harness.agents.values()
                        },
                    },
                    "progress": events.summary(),
                    "artifacts": [
                        {
                            "handle": artifact.handle,
                            "kind": artifact.kind,
                            "producer_agent": artifact.producer_agent,
                            "task_id": artifact.task_id,
                            "source_sequence": artifact.source_sequence,
                            "bytes": len(artifact.content),
                            "sha256": artifact.sha256,
                            "shared": artifact.shared,
                            **(
                                {
                                    "content": _bounded_utf8(
                                        artifact.content.decode(
                                            "utf-8", errors="replace"
                                        ),
                                        2_400,
                                    )
                                }
                                if artifact.kind
                                in (
                                    "build_diagnostic",
                                    "run_log",
                                    "test_result",
                                    "subtask_report",
                                    "team_summary",
                                    "final",
                                )
                                else {}
                            ),
                        }
                        for artifact in sorted(
                            with_harness.store._artifacts.values(),
                            key=lambda item: item.handle,
                        )
                        if artifact.sealed
                    ],
                }
                if record.result_artifact:
                    artifact = with_harness.store.get(record.result_artifact)
                    result.update(
                        artifact=artifact.handle,
                        sha256=artifact.sha256,
                        content=artifact.content.decode("utf-8", errors="replace"),
                    )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                return 0 if record.terminal_status == "ok" else 1
            time.sleep(0.05)
        raise HarnessError("workflow_timeout")
    except KeyboardInterrupt:
        events.emit(
            "harness", "workflow_failed", "Workflow interrupted by the user",
            reason="keyboard_interrupt",
        )
        return 130
    except Exception as error:
        events.emit(
            "harness", "workflow_failed",
            f"Workflow failed: {type(error).__name__}",
            detail=_bounded_utf8(str(error), 240),
        )
        raise
    finally:
        if monitor is not None:
            monitor.stop()
        try:
            if with_harness is not None:
                with_harness.close()
            elif native_channel is not None:
                native_channel.close()
        finally:
            events.close()


if __name__ == "__main__":
    raise SystemExit(main())
