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
from pathlib import Path
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
    import agentos_native_task_channel as native_task
    import agentos_workspace as workspace
    import guest_llm_relay as relay
except ModuleNotFoundError:  # pragma: no cover
    from . import agentos_nexus_dev as development
    from . import agentos_native_task_channel as native_task
    from . import agentos_workspace as workspace
    from . import guest_llm_relay as relay


MAX_AGENTS: Final = 8
MAX_TASKS: Final = 64
MAX_ARTIFACT_BYTES: Final = 64 * 1024
MAX_ARTIFACTS: Final = 128
MAX_MODEL_PROJECTION_BYTES: Final = 12 * 1024
MAX_ROUNDS: Final = 64
DEFAULT_HEARTBEAT_SECONDS: Final = 0.25

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
    parent_task_id: int
    parent_agent: int
    target_agent: int
    objective_artifact: int
    input_artifact: int
    required_capabilities: frozenset[str]
    allowed_tools: frozenset[str]
    workspace_revision: str
    resource_budget: int
    read_budget: int
    deadline_monotonic: float
    expected_result_kind: str


@dataclass(slots=True)
class TaskRecord:
    descriptor: TaskDescriptor
    state: str = "pending"
    result_artifact: int = 0
    terminal_status: str = ""
    claimed_by: int = 0
    model_rounds: int = 0
    tool_calls: int = 0


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
            if record is None or record.state != "claimed" or record.claimed_by != agent_id:
                raise HarnessError("task_completion_denied")
            if status == "ok":
                artifact = self.store.get(result_artifact)
                if (
                    artifact.producer_agent != agent_id
                    or artifact.task_id != task_id
                    or artifact.kind != record.descriptor.expected_result_kind
                    or not artifact.sealed
                ):
                    raise HarnessError("task_result_invalid")
            elif result_artifact:
                raise HarnessError("failed_task_has_result")
            if self.native is not None:
                self.native.complete(task_id, agent_id, status, result_artifact)
            record.result_artifact = result_artifact
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
                self.native.complete(
                    task_id, record.claimed_by, "cancelled", 0
                )
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
        for handle in handles[-8:]:
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
                action = dict(self.model(self._projection(task)))
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
        self.development = development.NexusDevelopmentBroker(self.root)
        self.agents: dict[int, AgentLoop] = {}
        self.shared_context: list[dict[str, object]] = []
        self._context_lock = threading.RLock()
        self.stopped = False
        self.model_factory = model_factory
        self.trace_path = trace_path
        self._trace_lock = threading.Lock()
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.trace_path.write_bytes(b"")
        self._next_agent = 1
        self._next_task = 1
        self._lock = threading.RLock()

    def trace_event(self, agent_id: int, row: Mapping[str, object]) -> None:
        if self.trace_path is None:
            return
        line = _canonical({"agent_id": agent_id, **dict(row)}) + b"\n"
        with self._trace_lock:
            with self.trace_path.open("ab") as stream:
                stream.write(line)

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

    def spawn(
        self, config: AgentConfig, model: Model | None = None, label: str = "agent"
    ) -> AgentLoop:
        config.validate(self.policy)
        if model is None:
            if self.model_factory is None:
                raise HarnessError("model_factory_missing")
            model = self.model_factory(config)
        with self._lock:
            if len(self.agents) >= MAX_AGENTS:
                raise HarnessError("agent_capacity")
            agent_id = self._next_agent
            self._next_agent += 1
            agent = AgentLoop(self, agent_id, config, model, label)
            if self.native_channel is not None:
                identity = self.native_channel.spawn(agent_id, config)
                agent.native_pid = identity["pid"]
                agent.native_agent_id = identity["agent_id"]
                agent.native_control_id = identity["control_id"]
            self.agents[agent_id] = agent
            agent.start()
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
        descriptor = TaskDescriptor(
            task_id=task_id,
            parent_task_id=0,
            parent_agent=agent.agent_id,
            target_agent=agent.agent_id,
            objective_artifact=artifact.handle,
            input_artifact=0,
            required_capabilities=agent.config.capabilities,
            allowed_tools=agent.config.tools,
            workspace_revision="",
            resource_budget=agent.config.resource_budget,
            read_budget=agent.config.artifact_read_limit,
            deadline_monotonic=time.monotonic() + deadline_seconds,
            expected_result_kind=expected_result_kind,
        )
        self.tasks.publish_root(descriptor)
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
        descriptor = TaskDescriptor(
            task_id,
            parent_task_id,
            parent.agent_id,
            target.agent_id,
            objective_artifact.handle,
            input_artifact,
            required_capabilities,
            allowed_tools,
            workspace_revision,
            resource_budget,
            read_budget,
            time.monotonic() + deadline_seconds,
            expected_result_kind,
        )
        self.tasks.delegate(parent, target, descriptor)
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

        builds: set[str] = set()
        cases: dict[str, set[str]] = {}
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
            for line in text.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields.setdefault(key, value)
            build_id = fields.get("build_id", "")
            if (
                artifact.kind == "build_diagnostic"
                and fields.get("status") == "passed"
                and build_id
            ):
                builds.add(build_id)
            if (
                artifact.kind == "test_result"
                and fields.get("status") == "passed"
                and build_id
                and fields.get("case_kind") in development.CASE_KINDS
            ):
                cases.setdefault(build_id, set()).add(fields["case_kind"])
        for build_id in builds:
            if development.CASE_KINDS <= cases.get(build_id, set()):
                return []
        missing = []
        if not builds:
            missing.append("successful_build")
        observed_cases = set().union(*(cases.values())) if cases else set()
        missing.extend(sorted(development.CASE_KINDS - observed_cases))
        return missing

    def execute_action(
        self, agent: AgentLoop, task: TaskRecord, action: Mapping[str, object]
    ) -> None:
        kind = action.get("type")
        if kind == "delegate":
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
            result = self.call_tool(tool, arguments)
            sequence = agent._append(
                "tool",
                task_id=task.descriptor.task_id,
                tool=tool,
                arguments=_argument_projection(arguments),
            )
            artifact_kind = {
                "write_file": "file",
                "apply_patch": "patch",
                "build_ucore_program": "build_diagnostic",
                "run_ucore_program": "test_result",
                "search_files": "search",
                "read_file": "file",
            }[tool]
            artifact = self.store.put(
                agent.config,
                agent.agent_id,
                task.descriptor.task_id,
                sequence,
                artifact_kind,
                result.content,
                shareable=True,
            )
            self.store.share(agent.config, agent.agent_id, artifact.handle)
            result_row = {
                "sequence": sequence,
                "kind": "tool_result",
                "event": "ARTIFACT_SHARED",
                "task_id": task.descriptor.task_id,
                "artifact": artifact.handle,
                "sha256": artifact.sha256,
                "workspace_revision": result.workspace_generation,
                "evidence": _result_projection(result.content),
            }
            agent.private_context.append(result_row)
            self.trace_event(agent.agent_id, result_row)
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
            artifact = self.store.put(
                agent.config,
                agent.agent_id,
                task.descriptor.task_id,
                sequence,
                task.descriptor.expected_result_kind,
                content,
                shareable=True,
            )
            self.store.share(agent.config, agent.agent_id, artifact.handle)
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

    def call_tool(self, tool: str, arguments: Mapping[str, object]) -> development.DevelopmentResult:
        if tool == "write_file":
            return self.development.write_file_chunk(
                arguments.get("path"), arguments.get("content"),
                arguments.get("expected_revision"),
                arguments.get("write_id"), arguments.get("commit"),
            )
        if tool == "apply_patch":
            return self.development.apply_patch(
                arguments.get("path"), arguments.get("patch"),
                arguments.get("expected_revision"),
            )
        if tool == "build_ucore_program":
            return self.development.build_ucore_program(
                arguments.get("source_path"), arguments.get("target")
            )
        if tool == "run_ucore_program":
            return self.development.run_ucore_program(
                arguments.get("build_id"), arguments.get("stdin"),
                arguments.get("expected_output"), arguments.get("expected_exit"),
                arguments.get("case_kind"),
            )
        if tool in ("search_files", "read_file"):
            # WorkspaceReader retains root/path/symlink/revision/range checks.
            reader = workspace.WorkspaceReader(self.root)
            try:
                if tool == "read_file":
                    observed = reader.read_file(
                        arguments.get("path"),
                        arguments.get("start_line", 1),
                        arguments.get("max_lines", 64),
                    )
                    try:
                        revision = self.development.program_revision(
                            arguments.get("path")
                        )
                    except (development.NexusDevelopmentError, OSError):
                        revision = ""
                    if revision:
                        observed = f"workspace_revision={revision}\n{observed}"
                else:
                    observed = reader.search_files(
                        arguments.get("query"), arguments.get("path", "")
                    )
            finally:
                close = getattr(reader, "close", None)
                if callable(close):
                    close()
            content = observed if isinstance(observed, str) else _canonical(observed).decode("utf-8")
            return development.DevelopmentResult("ok", "0" * 64, content)
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
        {"query": {"type": "string"}, "path": {"type": "string"}},
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
            "target": {"type": "string"},
        },
        ("source_path", "target"),
    ),
    "run_ucore_program": _object_schema(
        {
            "build_id": {"type": "string"},
            "stdin": {"type": "string"},
            "expected_output": {"type": "string"},
            "expected_exit": {"type": "integer"},
            "case_kind": {"type": "string", "enum": ["normal", "invalid", "failure"]},
        },
        ("build_id", "stdin", "expected_output", "expected_exit", "case_kind"),
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

TOOL_DESCRIPTIONS: Final = {
    "search_files": (
        "Search bounded UTF-8 files below the configured workspace root. "
        "Use this to locate examples before creating a program."
    ),
    "read_file": (
        "Read a bounded line range from one relative UTF-8 workspace file. "
        "max_lines must be between 1 and 64. Absolute paths, link escapes, and "
        "oversized output are rejected."
    ),
    "write_file": (
        "Atomically create or replace user/src/nexus_<name>_ucore.c. Supply "
        "expected_revision='missing' for a new file, or the exact 64-hex revision "
        "returned by the preceding write or patch. content is limited to 2400 "
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
        "with the fixed RISC-V toolchain. The target must equal the source stem. "
        "A successful result returns build_id and source_revision."
    ),
    "run_ucore_program": (
        "Start a fresh AgentOS-uCore Guest for an existing build_id, feed bounded "
        "serial input, and check output plus exit status. Run normal, invalid, and "
        "important failure cases before declaring completion."
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
            try:
                return self.provider.complete(
                    request, deadline_monotonic=time.monotonic() + 120.0
                )
            except relay.ProviderError as error:
                if error.code in (
                    "MULTIPLE_TOOL_CALLS",
                    "TOOL_ARGUMENT_SCHEMA_MISMATCH",
                    "INCOMPLETE_MODEL_RESPONSE",
                ) or not error.retryable:
                    raise
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _select_and_force_one_action(
        self,
        request: Mapping[str, object],
        tools: Sequence[Mapping[str, object]],
        deadline: float,
    ) -> relay.ModelReply:
        names = tuple(str(tool["name"]) for tool in tools)
        selector = dict(request)
        selector["corr_id"] = self._correlation()
        selector["tools"] = []
        selector["tool_choice"] = "none"
        selector["max_tokens"] = 64
        selector["system"] = (
            str(request["system"])
            + "\n\nThe previous response did not produce one admissible bounded "
            "action. Select only the "
            "single next action. Return exactly one action name with no punctuation "
            "or explanation. Allowed names: "
            + ", ".join((*names, "final"))
            + "."
        )
        selection = self._complete_with_retry(selector)
        if selection.type != "final":
            raise HarnessError("provider_action_selection_invalid")
        selected_text = selection.content.strip()
        selected = selected_text
        if selected not in (*names, "final"):
            candidates = {
                name
                for name in (*names, "final")
                if re.search(
                    rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                    selected_text,
                )
            }
            if len(candidates) == 1:
                selected = candidates.pop()
        if selected == "final":
            final_request = dict(request)
            final_request["corr_id"] = self._correlation()
            final_request["tools"] = []
            final_request["tool_choice"] = "none"
            final_request["system"] = (
                str(request["system"])
                + "\n\nReturn the final answer for this Agent task now. Do not emit "
                "tool calls or protocol markup."
            )
            return self._complete_with_retry(final_request)
        if selected not in names:
            raise HarnessError("provider_action_selection_invalid")
        forced = dict(request)
        forced["corr_id"] = self._correlation()
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
                + " The content field must stay below 2400 characters. If the "
                "complete file is longer, send only its first contiguous chunk "
                "with write_id='' and commit=0; continue from the returned write_id "
                "in later Agent Loop rounds. Do not include the remaining source now."
            )
        return self._complete_with_retry(forced)

    def __call__(self, projection: Mapping[str, object]) -> Mapping[str, object]:
        advertised_names = set(self.config.tools)
        context_rows = projection.get("context", [])
        if not isinstance(context_rows, list):
            context_rows = []
        if advertised_names.intersection(("write_file", "apply_patch")):
            observed_tools: list[str] = []
            if isinstance(context_rows, list):
                for row in context_rows:
                    if not isinstance(row, Mapping):
                        continue
                    tool = row.get("tool")
                    if isinstance(tool, str):
                        observed_tools.append(tool)
                    summary_tools = row.get("tools")
                    if isinstance(summary_tools, list):
                        observed_tools.extend(
                            value for value in summary_tools if isinstance(value, str)
                        )
            mutations = sum(
                isinstance(row, Mapping)
                and isinstance(row.get("evidence"), Mapping)
                and isinstance(row["evidence"].get("revision"), str)
                for row in context_rows
            )
            exploration = sum(
                name in ("search_files", "read_file") for name in observed_tools
            )
            if mutations == 0 and exploration >= 6:
                # Once enough examples have been inspected, only a real workspace
                # mutation can make a build or run meaningful.  Restricting the
                # advertised development action prevents an Agent from substituting
                # placeholder build ids or returning to an unbounded search loop.
                advertised_names.intersection_update(("write_file",))
        failed_build_revision = ""
        failed_build_index = -1
        for index, row in enumerate(context_rows):
            if not isinstance(row, Mapping) or row.get("kind") != "tool_result":
                continue
            evidence = row.get("evidence")
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
        tools = [
            {
                "name": name,
                "description": TOOL_DESCRIPTIONS[name],
                "input_schema": TOOL_SCHEMAS[name],
            }
            for name in sorted(advertised_names)
        ]
        if "ORCHESTRATE" in self.config.capabilities:
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
        system = (
            self.config.system_prompt
            + "\n\nYou are one instance of the common Nexus Agent Loop. Decide the next "
            "step from the objective, private Context, settled shared Context, and "
            "sealed Artifact projections. Use delegate_task only when parallel or "
            "specialized work is useful. Before reporting software development as "
            "complete, require a successful controlled build and real Guest evidence "
            "for normal, invalid, and important failure cases. Never assume a tool "
            "succeeded without its Artifact evidence. The private Context records "
            "prior tool arguments: do not repeat an identical read or search. For a "
            "development task, stop exploring after enough examples are available, "
            "then create or modify the program, build it, and run the required cases. "
            "If the current Context already contains six read/search calls and no "
            "successful workspace mutation, the next action must create the program "
            "or delegate that work to a writing Agent. New programs must use a new "
            "path matching user/src/nexus_<name>_ucore.c; never replace an existing "
            "application unless the objective explicitly names it. Keep the program "
            "concise; use staged write_file chunks only when it cannot fit one call. "
            "After a failed build, modify the exact source_path and revision named "
            "by that diagnostic before building again. If apply_patch reports "
            "patch_invalid, replace the full file through revision-checked write_file. "
            "AgentOS-uCore user programs may call only declarations present under "
            "user/include; hosted-C helpers such as exit, fgets, fflush, and strcspn "
            "are unavailable unless those headers declare them. Use the kernel user "
            "read/write interfaces and return an exit status when appropriate."
        )
        request = {
            "corr_id": 0,
            "model": self.model,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": _bounded_utf8(
                        _canonical(projection).decode("utf-8"),
                        MAX_MODEL_PROJECTION_BYTES,
                    ),
                }
            ],
            "tools": tools,
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
    native_channel = native_task.NativeTaskChannel(
        qemu=args.qemu,
        kernel=args.kernel,
        image=args.image,
        boot_timeout=args.native_boot_timeout,
    )
    with_harness: NexusHarness | None = None
    try:
        with_harness = NexusHarness(
            args.workspace,
            args.goal,
            policy,
            model_factory=factory,
            trace_path=args.trace_file,
            native_channel=native_channel,
        )
        root_agent = with_harness.spawn(root_config, label="configured-agent")
        root_task = with_harness.submit_root(root_agent)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            record = with_harness.tasks._tasks[root_task]
            if record.state == "terminal":
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
                        "agents": {
                            str(item.agent_id): {
                                "pid": item.native_pid,
                                "agent_id": item.native_agent_id,
                                "control_id": item.native_control_id,
                            }
                            for item in with_harness.agents.values()
                        },
                    },
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
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0 if record.terminal_status == "ok" else 1
            time.sleep(0.05)
        raise HarnessError("workflow_timeout")
    finally:
        if with_harness is not None:
            with_harness.close()
        else:
            native_channel.close()


if __name__ == "__main__":
    raise SystemExit(main())
