#!/usr/bin/env python3
"""Persistent QEMU bridge for the generic Harness native Task Channel."""

from __future__ import annotations

from collections import deque
import hashlib
from pathlib import Path
import queue
import re
import threading
import time
from typing import Final, Mapping

try:
    import guest_llm_relay as relay
except ModuleNotFoundError:  # pragma: no cover
    from . import guest_llm_relay as relay


READY: Final = "AGENT_HARNESS READY"
MAX_LOG_BYTES: Final = 64 * 1024
TICKS_PER_SECOND: Final = 100

CAPABILITY_BITS: Final = {
    "READ_CONTEXT": (1 << 0) | (1 << 1),
    "READ_WORKSPACE": (1 << 0) | (1 << 1),
    "WRITE_WORKSPACE": (1 << 5) | (1 << 8) | (1 << 14),
    "BUILD": (1 << 5) | (1 << 6),
    "RUN": (1 << 5) | (1 << 6),
    "ORCHESTRATE": (1 << 9) | (1 << 11) | (1 << 12),
    "SHARE_ARTIFACT": 1 << 6,
    "PREFETCH": 1 << 15,
}

TOOL_IDS: Final = {
    "apply_patch": 27,
    "write_file": 28,
    "search_files": 29,
    "read_file": 30,
    "build_ucore_program": 32,
    "run_ucore_program": 33,
}

RESULT_KIND_IDS: Final = {
    "final": 1,
    "subtask_report": 2,
    "team_summary": 3,
}

STATUS_IDS: Final = {
    "ok": 0,
    "failed": -1,
    "timeout": -7,
    "cancelled": -10,
}


class NativeTaskChannelError(RuntimeError):
    """Native Guest authority failed closed."""


def _capability_mask(capabilities: frozenset[str]) -> int:
    mask = 0
    for capability in capabilities:
        try:
            mask |= CAPABILITY_BITS[capability]
        except KeyError as error:
            raise NativeTaskChannelError("native_capability_unknown") from error
    return mask


def _tool_mask(tools: frozenset[str]) -> int:
    mask = 0
    for tool in tools:
        try:
            tool_id = TOOL_IDS[tool]
        except KeyError as error:
            raise NativeTaskChannelError("native_tool_unknown") from error
        mask |= 1 << (tool_id - 1)
    return mask


def _revision_digest(revision: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", revision):
        return revision.lower()
    return hashlib.sha256(revision.encode("utf-8")).hexdigest()


class NativeTaskChannel:
    """Own one long-lived Guest and serialize native Task authority calls."""

    def __init__(
        self,
        *,
        qemu: str,
        kernel: Path,
        image: Path,
        boot_timeout: float = 150.0,
    ) -> None:
        if not kernel.is_file() or not image.is_file():
            raise NativeTaskChannelError("native_guest_artifact_missing")
        command = relay.build_qemu_command(
            relay._resolve_qemu(qemu), kernel=str(kernel), image=str(image)
        )
        self.process = relay.QemuSerialProcess(command)
        self.proc = self.process.start()
        self._lines: queue.Queue[str] = queue.Queue()
        self._stderr: deque[bytes] = deque()
        self._log_bytes = 0
        self._lock = threading.RLock()
        self._closed = False
        self._claimed: dict[int, int] = {}
        self.lifecycle = (0, 0)
        self.native_root_agent = 0
        self.native_root_control = 0
        assert self.proc.stdout is not None and self.proc.stderr is not None
        self._reader(self.proc.stdout, True)
        self._reader(self.proc.stderr, False)
        deadline = time.monotonic() + boot_timeout
        line = self._wait_prefix(READY, deadline)
        fields = line.split()
        if len(fields) != 6:
            self.close()
            raise NativeTaskChannelError("native_ready_malformed")
        self.lifecycle = (int(fields[2]), int(fields[3]))
        self.native_root_agent = int(fields[4])
        self.native_root_control = int(fields[5])
        if min(*self.lifecycle, self.native_root_agent, self.native_root_control) <= 0:
            self.close()
            raise NativeTaskChannelError("native_ready_identity_invalid")

    def _reader(self, stream, stdout: bool) -> None:
        def run() -> None:
            while True:
                try:
                    line = stream.readline()
                except OSError:
                    line = b""
                if not line:
                    self._lines.put("AGENT_HARNESS EOF")
                    return
                if stdout:
                    decoded = line.decode("utf-8", errors="replace").strip("\r\n")
                    if decoded.startswith("AGENT_HARNESS "):
                        self._lines.put(decoded)
                elif self._log_bytes < MAX_LOG_BYTES:
                    kept = line[: MAX_LOG_BYTES - self._log_bytes]
                    self._stderr.append(kept)
                    self._log_bytes += len(kept)

        threading.Thread(
            target=run,
            name="agent-harness-native-stdout" if stdout else "agent-harness-native-stderr",
            daemon=True,
        ).start()

    def _write(self, line: str) -> None:
        if self._closed:
            raise NativeTaskChannelError("native_guest_closed")
        relay._write_process_before_deadline(
            self.process,
            line.encode("ascii") + b"\n",
            deadline_monotonic=time.monotonic() + 5.0,
        )

    def _wait_prefix(self, prefix: str, deadline: float) -> str:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeTaskChannelError("native_guest_timeout")
            try:
                line = self._lines.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise NativeTaskChannelError("native_guest_exited")
                continue
            if line == "AGENT_HARNESS EOF":
                raise NativeTaskChannelError("native_guest_eof")
            if line.startswith("AGENT_HARNESS ERROR "):
                raise NativeTaskChannelError(line.removeprefix("AGENT_HARNESS ERROR "))
            if line.startswith(prefix + " ") or line == prefix:
                return line
            raise NativeTaskChannelError(f"native_guest_out_of_order:{line}")

    def _request(self, command: str, response: str, timeout: float = 30.0) -> str:
        with self._lock:
            self._write(command)
            return self._wait_prefix(response, time.monotonic() + timeout)

    def spawn(self, host_agent_id: int, config: object) -> Mapping[str, int]:
        caps = _capability_mask(getattr(config, "capabilities"))
        tools = _tool_mask(getattr(config, "tools"))
        line = self._request(
            "SPAWN "
            f"{host_agent_id} 0x{caps:x} 0x{tools:x} "
            f"{int(getattr(config, 'resource_budget'))} "
            f"{int(getattr(config, 'artifact_count_limit'))} "
            f"{int(getattr(config, 'artifact_bytes_limit'))} "
            f"{int(getattr(config, 'artifact_read_limit'))} "
            f"{int(getattr(config, 'summary_high_watermark'))}",
            "AGENT_HARNESS SPAWN",
        )
        fields = line.split()
        if len(fields) != 6 or int(fields[2]) != host_agent_id:
            raise NativeTaskChannelError("native_spawn_mismatch")
        return {
            "pid": int(fields[3]),
            "agent_id": int(fields[4]),
            "control_id": int(fields[5]),
        }

    def _tick(self) -> int:
        line = self._request("TICK", "AGENT_HARNESS TICK", timeout=5.0)
        fields = line.split()
        if len(fields) != 3:
            raise NativeTaskChannelError("native_tick_malformed")
        return int(fields[2])

    def delegate(self, descriptor: object) -> None:
        task_id = int(getattr(descriptor, "task_id"))
        target_agent = int(getattr(descriptor, "target_agent"))
        if task_id in self._claimed:
            raise NativeTaskChannelError("native_task_reused")
        remaining = max(0.0, float(getattr(descriptor, "deadline_monotonic")) - time.monotonic())
        deadline_tick = self._tick() + max(1, int(remaining * TICKS_PER_SECOND))
        result_kind = RESULT_KIND_IDS[str(getattr(descriptor, "expected_result_kind"))]
        command = (
            "DELEGATE "
            f"{task_id} {int(getattr(descriptor, 'parent_task_id'))} "
            f"{int(getattr(descriptor, 'parent_agent'))} {target_agent} "
            f"{int(getattr(descriptor, 'objective_artifact'))} "
            f"{int(getattr(descriptor, 'input_artifact'))} "
            f"0x{_capability_mask(getattr(descriptor, 'required_capabilities')):x} "
            f"0x{_tool_mask(getattr(descriptor, 'allowed_tools')):x} "
            f"{_revision_digest(str(getattr(descriptor, 'workspace_revision')))} "
            f"{int(getattr(descriptor, 'resource_budget'))} "
            f"{int(getattr(descriptor, 'read_budget'))} {deadline_tick} {result_kind}"
        )
        line = self._request(command, "AGENT_HARNESS CLAIM")
        fields = line.split()
        if len(fields) != 5 or int(fields[2]) != task_id or int(fields[4]) != target_agent:
            raise NativeTaskChannelError("native_claim_mismatch")
        self._claimed[task_id] = target_agent

    def claim(self, task_id: int, agent_id: int) -> None:
        if self._claimed.get(task_id) != agent_id:
            raise NativeTaskChannelError("native_claim_identity_mismatch")

    def complete(self, task_id: int, agent_id: int, status: str, result_artifact: int) -> None:
        self.claim(task_id, agent_id)
        try:
            native_status = STATUS_IDS[status]
        except KeyError as error:
            raise NativeTaskChannelError("native_terminal_status_unknown") from error
        line = self._request(
            f"COMPLETE {task_id} {native_status} {result_artifact}",
            "AGENT_HARNESS COMPLETE",
        )
        fields = line.split()
        if len(fields) != 6 or int(fields[2]) != task_id or int(fields[3]) != native_status:
            raise NativeTaskChannelError("native_completion_mismatch")
        self._claimed.pop(task_id, None)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.proc.poll() is None and not self._claimed:
                self._request("CLOSE", "AGENT_HARNESS CLOSED", timeout=10.0)
        except (NativeTaskChannelError, OSError, RuntimeError, relay.RelayError):
            pass
        finally:
            self._closed = True
            self.process.stop()

    def __enter__(self) -> "NativeTaskChannel":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


__all__ = [
    "NativeTaskChannel",
    "NativeTaskChannelError",
]
