#!/usr/bin/env python3
"""Bounded progress events and terminal renderers for the Nexus Harness."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Final, Mapping, TextIO


PROGRESS_MODES: Final = ("auto", "plain", "dashboard", "ndjson", "off")
EVENT_SOURCES: Final = frozenset(
    ("harness", "model", "agent", "task", "tool", "build", "run", "kernel", "qemu")
)
RECENT_EVENTS: Final = 10
MAX_EVENT_MESSAGE_BYTES: Final = 320
MAX_GOAL_BYTES: Final = 240
RESERVED_FIELDS: Final = frozenset(
    (
        "schema",
        "progress_sequence",
        "time_unix_ms",
        "elapsed_ms",
        "source",
        "kind",
        "message",
    )
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bounded_utf8(value: object, maximum: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= maximum:
        return text
    marker = b"..."
    raw = raw[: maximum - len(marker)]
    while raw:
        try:
            return raw.decode("utf-8") + marker.decode("ascii")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return marker.decode("ascii")


def _elapsed_text(milliseconds: int) -> str:
    seconds, millis = divmod(max(0, milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _event_source(kind: str) -> str:
    if kind.startswith("task_"):
        return "task"
    if kind in ("tool", "tool_result"):
        return "tool"
    if kind in ("heartbeat", "user", "error", "summary"):
        return "agent"
    if kind in ("workflow_summary", "artifact_accepted"):
        return "harness"
    return "agent"


def _context_message(kind: str, fields: Mapping[str, object]) -> str:
    task = fields.get("task_id", "")
    if kind == "heartbeat":
        return "Agent waiting for work"
    if kind == "task_claimed":
        return f"Task {task} claimed"
    if kind == "task_delegated":
        return f"Task {task} delegated"
    if kind == "task_completed":
        return f"Task {task} completed with {fields.get('status', 'unknown')}"
    if kind == "task_wait":
        return f"Task {task} waiting for child tasks"
    if kind == "tool":
        return f"Selected tool {fields.get('tool', 'unknown')}"
    if kind == "tool_result":
        return f"Tool result sealed as artifact {fields.get('artifact', 0)}"
    if kind == "workflow_summary":
        return "Workflow summary committed"
    if kind == "error":
        return f"Agent error: {fields.get('detail', fields.get('error', 'unknown'))}"
    return kind.replace("_", " ")


class HarnessEventBus:
    """Serialize progress once, then fan it out to trace and terminal views."""

    def __init__(
        self,
        *,
        mode: str = "off",
        goal: str = "",
        trace_path: Path | None = None,
        stream: TextIO | None = None,
        clock=time.monotonic,
        wall_clock=time.time,
    ) -> None:
        if mode not in PROGRESS_MODES:
            raise ValueError("progress_mode_invalid")
        self.stream = sys.stderr if stream is None else stream
        if mode == "auto":
            is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
            mode = "dashboard" if is_tty else "plain"
        self.mode = mode
        self.goal = _bounded_utf8(goal, MAX_GOAL_BYTES)
        self.trace_path = trace_path
        self._clock = clock
        self._wall_clock = wall_clock
        self._started = clock()
        self._lock = threading.RLock()
        self._sequence = 0
        self._closed = False
        self._dashboard_started = False
        self._last_dashboard_render = 0.0
        self._last_plain_kernel = -10_000
        self._recent: deque[dict[str, object]] = deque(maxlen=RECENT_EVENTS)
        self._agents: dict[int, dict[str, object]] = {}
        self._tasks: dict[int, dict[str, object]] = {}
        self._kernel: dict[str, object] = {}
        self._current: dict[str, object] = {}
        self._model = ""
        self._qemu = "STARTING"
        self._artifact_count = 0
        self._artifact_bytes = 0
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.trace_path.write_bytes(b"")

    @property
    def event_count(self) -> int:
        with self._lock:
            return self._sequence

    def summary(self) -> dict[str, object]:
        with self._lock:
            return {
                "mode": self.mode,
                "event_count": self._sequence,
                "artifact_count": self._artifact_count,
                "artifact_bytes": self._artifact_bytes,
                "kernel_status": dict(self._kernel),
            }

    def emit(
        self,
        source: str,
        kind: str,
        message: str,
        **fields: object,
    ) -> dict[str, object]:
        with self._lock:
            if self._closed:
                return {}
            if source not in EVENT_SOURCES:
                raise ValueError("progress_source_invalid")
            self._sequence += 1
            event: dict[str, object] = {
                "schema": 1,
                "progress_sequence": self._sequence,
                "time_unix_ms": int(self._wall_clock() * 1000),
                "elapsed_ms": int((self._clock() - self._started) * 1000),
                "source": _bounded_utf8(source, 32),
                "kind": _bounded_utf8(kind, 64),
                "message": _bounded_utf8(message, MAX_EVENT_MESSAGE_BYTES),
            }
            for key, value in fields.items():
                if key not in RESERVED_FIELDS:
                    event[key] = value
            self._update_state(event)
            if kind not in ("heartbeat", "kernel_status"):
                self._recent.append(event)
            line = _canonical(event)
            if self.trace_path is not None:
                with self.trace_path.open("ab") as trace:
                    trace.write(line.encode("utf-8") + b"\n")
            self._render(event, line)
            return event

    def emit_context(self, agent_id: int, row: Mapping[str, object]) -> None:
        kind = str(row.get("kind", "agent_event"))
        fields = {
            key: value
            for key, value in row.items()
            if key not in ("kind", "agent_id")
        }
        self.emit(
            _event_source(kind),
            kind,
            _context_message(kind, fields),
            agent_id=agent_id,
            **fields,
        )

    def _update_state(self, event: Mapping[str, object]) -> None:
        source = str(event.get("source", ""))
        kind = str(event.get("kind", ""))
        if event.get("model"):
            self._model = str(event["model"])
        agent_id = event.get("agent_id")
        task_id = event.get("task_id")
        if isinstance(agent_id, int) and agent_id > 0:
            agent = self._agents.setdefault(agent_id, {"state": "STARTING"})
            for key in (
                "label",
                "native_pid",
                "native_agent_id",
                "native_control_id",
                "model_round",
                "tool_calls",
                "task_id",
                "tool",
            ):
                if key in event:
                    agent[key] = event[key]
            states = {
                "agent_spawned": "READY",
                "heartbeat": "WAITING",
                "task_claimed": "RUNNING",
                "model_started": "WAITING_MODEL",
                "model_completed": "DECIDING",
                "model_failed": "FAILED",
                "tool_started": "RUNNING_TOOL",
                "tool_completed": "RUNNING",
                "task_wait": "WAITING_TASK",
                "task_completed": "COMPLETED",
                "error": "FAILED",
            }
            if kind in states:
                agent["state"] = states[kind]
        if isinstance(task_id, int) and task_id > 0:
            task = self._tasks.setdefault(task_id, {"state": "PENDING"})
            for key in ("parent_task_id", "target_agent", "parent_agent", "status"):
                if key in event:
                    task[key] = event[key]
            if kind in ("root_task_submitted", "task_delegated"):
                task["state"] = "PENDING"
            elif kind in ("task_claimed", "native_task_claimed"):
                task["state"] = "CLAIMED"
            elif kind == "task_wait":
                task["state"] = "WAITING"
            elif kind in ("task_completed", "native_task_completed"):
                task["state"] = "TERMINAL"
        if source == "kernel" and kind == "kernel_status":
            self._kernel = {
                key: value
                for key, value in event.items()
                if key not in RESERVED_FIELDS
            }
            self._qemu = "RUNNING"
        if kind == "qemu_starting":
            self._qemu = "STARTING"
            self._model = str(event.get("model", self._model))
        elif kind == "guest_ready":
            self._qemu = "RUNNING"
        elif kind in ("workflow_completed", "guest_closed"):
            self._qemu = "CLOSING" if kind == "workflow_completed" else "CLOSED"
        elif kind in ("workflow_failed", "qemu_failed"):
            self._qemu = "FAILED"
        if kind == "artifact_sealed":
            self._artifact_count = max(
                self._artifact_count, int(event.get("artifact_count", 0))
            )
            self._artifact_bytes += max(0, int(event.get("bytes", 0)))
        if kind in (
            "model_started",
            "tool_started",
            "build_worktree_started",
            "build_command_started",
            "run_guest_started",
        ):
            self._current = dict(event)
        elif kind in ("tool_completed", "workflow_completed", "workflow_failed"):
            self._current = {}

    def _render(self, event: Mapping[str, object], line: str) -> None:
        if self.mode == "off":
            return
        if self.mode == "ndjson":
            self.stream.write(line + "\n")
            self.stream.flush()
            return
        if self.mode == "plain":
            if event.get("kind") == "heartbeat":
                return
            if event.get("kind") == "kernel_status":
                elapsed = int(event.get("elapsed_ms", 0))
                if elapsed - self._last_plain_kernel < 5_000:
                    return
                self._last_plain_kernel = elapsed
            elapsed = _elapsed_text(int(event.get("elapsed_ms", 0)))
            source = str(event.get("source", "harness")).upper()
            self.stream.write(
                f"[{elapsed}] [{source}] {event.get('message', '')}\n"
            )
            self.stream.flush()
            return
        now = self._clock()
        urgent = event.get("kind") in (
            "workflow_completed",
            "workflow_failed",
            "error",
        )
        if not urgent and now - self._last_dashboard_render < 0.08:
            return
        self._last_dashboard_render = now
        self._render_dashboard(event)

    @staticmethod
    def _clip(value: object, width: int) -> str:
        text = _bounded_utf8(value, max(8, width * 3))
        if len(text) <= width:
            return text
        return text[: max(1, width - 3)] + "..."

    def _render_dashboard(self, event: Mapping[str, object]) -> None:
        width, height = shutil.get_terminal_size((120, 34))
        compact = width < 96 or height < 24
        width = max(48, width)
        if not self._dashboard_started:
            self.stream.write("\x1b[?25l\x1b[2J")
            self._dashboard_started = True
        lines = [
            "Nexus Harness".ljust(max(1, width - 24))
            + f"elapsed {_elapsed_text(int(event.get('elapsed_ms', 0)))}",
            f"Goal  {self._clip(self.goal, width - 6)}",
            (
                f"QEMU  {self._qemu:<9}  Model {self._clip(self._model or '-', 24):<24}  "
                f"Events {self._sequence}  Artifacts {self._artifact_count}/"
                f"{self._artifact_bytes}B"
            ),
        ]
        kernel = self._kernel
        if kernel:
            lines.append(
                "Guest "
                f"lifecycle={kernel.get('lifecycle_id', 0)}/{kernel.get('lifecycle_generation', 0)} "
                f"tick={kernel.get('tick', 0)} tasks="
                f"{kernel.get('tasks_pending', 0)}/"
                f"{kernel.get('tasks_claimed', 0)}/"
                f"{kernel.get('tasks_terminal', 0)} "
                f"sq={kernel.get('sq_depth', 0)} cq={kernel.get('cq_depth', 0)} "
                f"context={kernel.get('context_count', 0)} waiting={kernel.get('wait_sleep_count', 0)}"
            )
            lines.append(
                "Sched "
                f"runnable={kernel.get('scheduler_runnable', 0)} "
                f"vruntime={kernel.get('scheduler_vruntime', 0)} "
                f"deadline={kernel.get('scheduler_virtual_deadline', 0)} "
                f"service={kernel.get('scheduler_service_cycles', 0)}"
            )
        if compact:
            agent_states = ", ".join(
                f"{agent_id}:{agent.get('state', 'UNKNOWN')}"
                for agent_id, agent in sorted(self._agents.items())[:4]
            ) or "-"
            task_states = ", ".join(
                f"{task_id}:{task.get('state', 'UNKNOWN')}"
                for task_id, task in sorted(self._tasks.items())[-4:]
            ) or "-"
            lines.extend((f"Agents {agent_states}", f"Tasks  {task_states}"))
            if self._current:
                lines.append(
                    "Now    "
                    + self._clip(self._current.get("message", ""), width - 7)
                )
            lines.append("")
            room = max(2, height - len(lines))
            for recent in list(self._recent)[-room:]:
                elapsed = _elapsed_text(int(recent.get("elapsed_ms", 0)))
                source = str(recent.get("source", "")).upper()
                prefix = f"{elapsed} [{source}] "
                lines.append(
                    prefix
                    + self._clip(recent.get("message", ""), width - len(prefix))
                )
            rendered = "\n".join(
                self._clip(line, width) for line in lines[:height]
            )
            self.stream.write("\x1b[H" + rendered + "\x1b[J")
            self.stream.flush()
            return
        lines.extend(("", "Agents", "ID  PID   State          Task  Round  Calls  Current"))
        for agent_id, agent in sorted(self._agents.items())[:8]:
            lines.append(
                f"{agent_id:<3} {int(agent.get('native_pid', 0)):<5} "
                f"{str(agent.get('state', 'UNKNOWN')):<14} "
                f"{int(agent.get('task_id', 0)):<5} "
                f"{int(agent.get('model_round', 0)):<6} "
                f"{int(agent.get('tool_calls', 0)):<6} "
                f"{self._clip(agent.get('tool', agent.get('label', '')), 28)}"
            )
        lines.extend(("", "Tasks", "ID  Parent  Agent  State       Status"))
        for task_id, task in sorted(self._tasks.items())[-8:]:
            lines.append(
                f"{task_id:<3} {int(task.get('parent_task_id', 0)):<7} "
                f"{int(task.get('target_agent', 0)):<6} "
                f"{str(task.get('state', 'UNKNOWN')):<11} "
                f"{self._clip(task.get('status', ''), 18)}"
            )
        if self._current:
            lines.extend(
                (
                    "",
                    "Current",
                    self._clip(self._current.get("message", ""), width),
                )
            )
        lines.extend(("", "Recent events"))
        room = max(3, height - len(lines) - 1)
        for recent in list(self._recent)[-room:]:
            elapsed = _elapsed_text(int(recent.get("elapsed_ms", 0)))
            source = str(recent.get("source", "")).upper()
            prefix = f"{elapsed} [{source}] "
            lines.append(prefix + self._clip(recent.get("message", ""), width - len(prefix)))
        rendered = "\n".join(self._clip(line, width) for line in lines[:height])
        self.stream.write("\x1b[H" + rendered + "\x1b[J")
        self.stream.flush()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._dashboard_started:
                self.stream.write("\x1b[?25h\n")
                self.stream.flush()
            self._closed = True


__all__ = ["EVENT_SOURCES", "HarnessEventBus", "PROGRESS_MODES"]
