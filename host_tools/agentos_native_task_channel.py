#!/usr/bin/env python3
"""Persistent QEMU bridge for the generic Harness native Task Channel."""

from __future__ import annotations

from collections import deque
import hashlib
from pathlib import Path
import queue
import re
import secrets
import shutil
import tempfile
import threading
import time
from typing import Callable, Final, Mapping, Sequence

try:
    import guest_llm_relay as relay
except ModuleNotFoundError:  # pragma: no cover
    from . import guest_llm_relay as relay


READY: Final = "AGENT_HARNESS READY"
MAX_LOG_BYTES: Final = 64 * 1024
TICKS_PER_SECOND: Final = 100
STATUS_VERSION: Final = 2
STATUS_FIELDS: Final = frozenset(
    (
        "version", "tick", "lifecycle_id", "lifecycle_generation",
        "agents_active", "tasks_active", "tasks_pending", "tasks_claimed",
        "tasks_terminal", "sq_depth", "cq_depth",
        "submitted", "completed", "context_count", "context_latest",
        "context_dropped", "artifact_count", "artifact_bytes",
        "catalog_state", "catalog_records", "catalog_candidates",
        "catalog_reuse", "catalog_watch_events",
        "loop_state", "wait_count", "wait_sleep_count",
        "wait_wakeup_count", "task_wait_count", "last_heartbeat_tick", "scheduler_runnable",
        "scheduler_vruntime", "scheduler_virtual_deadline",
        "scheduler_service_cycles", "resource_account_slot",
        "resource_account_generation",
    )
)

NativeEventCallback = Callable[
    [str, str, str, Mapping[str, object]], None
]

CAPABILITY_BITS: Final = {
    "READ_CONTEXT": (1 << 0) | (1 << 1),
    # READ_WORKSPACE also authorizes the provider's private Catalog rows and
    # Typed Watch.  Host workspace bytes remain read-only under this capability.
    "READ_WORKSPACE": (1 << 0) | (1 << 1) | (1 << 4) | (1 << 8),
    "WRITE_WORKSPACE": (1 << 5) | (1 << 6) | (1 << 8) | (1 << 14),
    "BUILD": (1 << 1) | (1 << 5) | (1 << 6) | (1 << 14),
    "RUN": (1 << 1) | (1 << 6),
    "ORCHESTRATE": (1 << 9) | (1 << 11) | (1 << 12),
    "SHARE_ARTIFACT": 1 << 6,
    "PREFETCH": 1 << 15,
}

TOOL_IDS: Final = {
    "delegate_task": 26,
    "apply_patch": 27,
    "write_file": 28,
    "search_files": 29,
    "read_file": 30,
    "build_ucore_program": 32,
    "run_ucore_program": 33,
}

ARTIFACT_KIND_IDS: Final = {
    "user": 1,
    "tool": 2,
    "final": 3,
    "file": 4,
    "search": 5,
    "patch": 6,
    "build_diagnostic": 7,
    "run_log": 8,
    "test_result": 9,
    "subtask_report": 10,
    "team_summary": 11,
}

ARTIFACT_F_UTF8: Final = 1 << 0
ARTIFACT_F_SHAREABLE: Final = 1 << 1
ARTIFACT_CHUNK_BYTES: Final = 240

STATUS_IDS: Final = {
    "ok": 0,
    "failed": -1,
    "timeout": -7,
    "cancelled": -10,
}
STATUS_NAMES: Final = {value: name for name, value in STATUS_IDS.items()}


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
        event_callback: NativeEventCallback | None = None,
    ) -> None:
        if not kernel.is_file() or not image.is_file():
            raise NativeTaskChannelError("native_guest_artifact_missing")
        self._guest_directory = tempfile.TemporaryDirectory(
            prefix="agentos-harness-guest-"
        )
        guest_image = Path(self._guest_directory.name) / "fs.img"
        try:
            shutil.copyfile(image, guest_image)
        except OSError as error:
            self._guest_directory.cleanup()
            raise NativeTaskChannelError("native_guest_image_copy_failed") from error
        command = relay.build_qemu_command(
            relay._resolve_qemu(qemu), kernel=str(kernel), image=str(guest_image)
        )
        self._event_callback = event_callback
        self._emit(
            "qemu", "qemu_starting", "Starting the persistent Harness Guest",
            kernel=str(kernel), image="isolated-control-guest",
        )
        self.process = relay.QemuSerialProcess(command)
        self.proc = self.process.start()
        self._lines: queue.Queue[str] = queue.Queue()
        self._stderr: deque[bytes] = deque()
        self._stdout_tail: deque[str] = deque(maxlen=80)
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
        self._emit(
            "kernel", "guest_ready",
            "Harness Guest and native Task Channel are ready",
            lifecycle_id=self.lifecycle[0],
            lifecycle_generation=self.lifecycle[1],
            native_root_agent=self.native_root_agent,
            native_root_control=self.native_root_control,
        )

    def _emit(
        self, source: str, kind: str, message: str, **fields: object
    ) -> None:
        if self._event_callback is None:
            return
        try:
            self._event_callback(source, kind, message, fields)
        except Exception:
            # Observability must never acquire authority over Guest transitions.
            pass

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
                    self._stdout_tail.append(decoded)
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

    def diagnostic_tail(self) -> tuple[str, ...]:
        """Return the bounded Guest console tail for test failure diagnosis."""
        return tuple(self._stdout_tail)

    def spawn(
        self, host_agent_id: int, config: object, *, channel_owner: bool = True
    ) -> Mapping[str, int]:
        self._emit(
            "kernel", "native_spawn_started",
            f"Creating Guest identity for Host Agent {host_agent_id}",
            agent_id=host_agent_id,
        )
        caps = _capability_mask(getattr(config, "capabilities"))
        tools = _tool_mask(getattr(config, "tools"))
        line = self._request(
            "SPAWN "
            f"{host_agent_id} 0x{caps:x} 0x{tools:x} "
            f"{int(getattr(config, 'resource_budget'))} "
            f"{int(getattr(config, 'artifact_count_limit'))} "
            f"{int(getattr(config, 'artifact_bytes_limit'))} "
            f"{int(getattr(config, 'artifact_read_limit'))} "
            f"{int(getattr(config, 'summary_high_watermark'))} "
            f"{1 if channel_owner else 0}",
            "AGENT_HARNESS SPAWN",
        )
        fields = line.split()
        if len(fields) != 6 or int(fields[2]) != host_agent_id:
            raise NativeTaskChannelError("native_spawn_mismatch")
        identity = {
            "pid": int(fields[3]),
            "agent_id": int(fields[4]),
            "control_id": int(fields[5]),
        }
        self._emit(
            "kernel", "native_spawn_completed",
            f"Guest identity for Host Agent {host_agent_id} is active",
            agent_id=host_agent_id,
            native_pid=identity["pid"],
            native_agent_id=identity["agent_id"],
            native_control_id=identity["control_id"],
        )
        return identity

    def _tick(self) -> int:
        line = self._request("TICK", "AGENT_HARNESS TICK", timeout=5.0)
        fields = line.split()
        if len(fields) != 3:
            raise NativeTaskChannelError("native_tick_malformed")
        return int(fields[2])

    def status(self) -> dict[str, int]:
        """Return one synchronous, versioned snapshot from the Guest kernel."""

        line = self._request("STATUS", "AGENT_HARNESS STATUS", timeout=5.0)
        parsed: dict[str, int] = {}
        for token in line.split()[2:]:
            if "=" not in token:
                raise NativeTaskChannelError("native_status_malformed")
            key, value = token.split("=", 1)
            if (
                key in parsed
                or key not in STATUS_FIELDS
                or not re.fullmatch(r"-?[0-9]+", value)
            ):
                raise NativeTaskChannelError("native_status_malformed")
            parsed[key] = int(value)
        if set(parsed) != STATUS_FIELDS or parsed["version"] != STATUS_VERSION:
            raise NativeTaskChannelError("native_status_version")
        if (
            parsed["lifecycle_id"] != self.lifecycle[0]
            or parsed["lifecycle_generation"] != self.lifecycle[1]
            or min(parsed["sq_depth"], parsed["cq_depth"]) < 0
        ):
            raise NativeTaskChannelError("native_status_identity_mismatch")
        return parsed

    @staticmethod
    def _parse_catalog(line: str, operation: int) -> dict[str, object]:
        fields = line.split()
        if len(fields) != 22 or int(fields[2]) != operation:
            raise NativeTaskChannelError("native_catalog_response_malformed")
        generation = fields[13]
        if not re.fullmatch(r"[0-9a-f]{64}", generation):
            raise NativeTaskChannelError("native_catalog_generation_malformed")
        identities: list[dict[str, int]] = []
        for field in fields[14:]:
            components = field.split(":")
            if len(components) != 3 or not all(
                re.fullmatch(r"[0-9]+", component) for component in components
            ):
                raise NativeTaskChannelError("native_catalog_identity_malformed")
            identities.append(
                {
                    "dev": int(components[0]),
                    "inum": int(components[1]),
                    "incarnation": int(components[2]),
                }
            )
        parsed: dict[str, object] = {
            "operation": int(fields[2]),
            "host_agent_id": int(fields[3]),
            "task_id": int(fields[4]),
            "status": int(fields[5]),
            "reuse": int(fields[6]),
            "records": int(fields[7]),
            "candidates": int(fields[8]),
            "used_index": int(fields[9]),
            "candidate_mask": int(fields[10]),
            "watch_events": int(fields[11]),
            "fs_generation": int(fields[12]),
            "workspace_generation": generation,
            "identities": identities,
        }
        if parsed["host_agent_id"] <= 0 or parsed["task_id"] <= 0:
            raise NativeTaskChannelError("native_catalog_identity_invalid")
        return parsed

    def _catalog_request(
        self,
        operation: int,
        command: str,
        *,
        host_agent_id: int,
        task_id: int,
    ) -> dict[str, object]:
        self.claim(task_id, host_agent_id)
        parsed = self._parse_catalog(
            self._request(command, "AGENT_HARNESS CATALOG"), operation
        )
        if (
            parsed["host_agent_id"] != host_agent_id
            or parsed["task_id"] != task_id
        ):
            raise NativeTaskChannelError("native_catalog_task_mismatch")
        return parsed

    def catalog_load(
        self,
        *,
        host_agent_id: int,
        task_id: int,
        workspace_generation: str,
        cursor: int,
        eof: bool,
        entries: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", workspace_generation)
            or not 1 <= len(entries) <= 8
            or type(cursor) is not int
            or cursor < 0
        ):
            raise NativeTaskChannelError("native_catalog_page_invalid")
        canonical_entries = [
            {
                "kind": str(entry["kind"]),
                "object_id": str(entry["object_id"]),
                "path": str(entry["path"]),
                "revision": str(entry["revision"]),
                "size": int(entry["size"]),
                "stage": str(entry["stage"]),
                "summary": str(entry["summary"]),
            }
            for entry in entries
        ]
        page_digest = hashlib.sha256(
            repr(canonical_entries).encode("utf-8")
        ).hexdigest()
        begin = self._catalog_request(
            8,
            "CATALOG_BEGIN "
            f"{host_agent_id} {task_id} {len(entries)} {cursor} "
            f"{1 if eof else 0} {workspace_generation} {page_digest}",
            host_agent_id=host_agent_id,
            task_id=task_id,
        )
        if begin["status"] != 0:
            raise NativeTaskChannelError(f"native_catalog_begin_failed:{begin}")
        if begin["reuse"]:
            self._emit(
                "kernel", "catalog_reused", "Guest reused a verified Catalog page",
                agent_id=host_agent_id, task_id=task_id, cursor=cursor,
                records=begin["records"], workspace_generation=workspace_generation,
            )
            return begin
        for index, entry in enumerate(canonical_entries):
            if (
                not re.fullmatch(r"[0-9a-f]{64}", entry["object_id"])
                or not re.fullmatch(r"[0-9a-f]{64}", entry["revision"])
                or not re.fullmatch(r"[a-z0-9_-]{1,15}", entry["stage"])
                or not re.fullmatch(r"[a-z0-9_-]{1,15}", entry["kind"])
            ):
                raise NativeTaskChannelError("native_catalog_entry_invalid")
            summary = entry["summary"].encode("utf-8")
            if not 1 <= len(summary) < 64:
                raise NativeTaskChannelError("native_catalog_summary_invalid")
            loaded = self._catalog_request(
                9,
                "CATALOG_ENTRY "
                f"{host_agent_id} {task_id} {index} {len(entries)} "
                f"{entry['size']} {entry['object_id']} {entry['revision']} "
                f"{entry['stage']} {entry['kind']} {summary.hex()}",
                host_agent_id=host_agent_id,
                task_id=task_id,
            )
            if loaded["status"] != 0:
                raise NativeTaskChannelError(
                    f"native_catalog_entry_failed:{index}:{loaded}"
                )
        committed = self._catalog_request(
            10,
            f"CATALOG_COMMIT {host_agent_id} {task_id}",
            host_agent_id=host_agent_id,
            task_id=task_id,
        )
        if (
            committed["status"] != 0
            or committed["used_index"] != 1
            or committed["candidates"] != len(entries)
            or committed["records"] <= committed["candidates"]
            or committed["workspace_generation"] != workspace_generation
        ):
            raise NativeTaskChannelError(
                f"native_catalog_commit_failed:{committed}"
            )
        self._emit(
            "kernel", "catalog_built", "Guest built and verified a Catalog page",
            agent_id=host_agent_id, task_id=task_id, cursor=cursor,
            records=committed["records"], candidates=committed["candidates"],
            used_index=1, watch_events=committed["watch_events"],
            workspace_generation=workspace_generation,
        )
        return committed

    def catalog_query(
        self,
        *,
        host_agent_id: int,
        task_id: int,
        stage: str = "",
        kind: str = "",
        status: str = "current",
        summary_contains: str = "",
    ) -> dict[str, object]:
        for value in (stage, kind, status):
            if value and not re.fullmatch(r"[a-z0-9_-]{1,15}", value):
                raise NativeTaskChannelError("native_catalog_query_invalid")
        summary = summary_contains.encode("utf-8")
        if len(summary) >= 64:
            raise NativeTaskChannelError("native_catalog_query_invalid")
        parsed = self._catalog_request(
            11,
            "CATALOG_QUERY "
            f"{host_agent_id} {task_id} {stage or '-'} {kind or '-'} "
            f"{status or '-'} {summary.hex() if summary else '-'}",
            host_agent_id=host_agent_id,
            task_id=task_id,
        )
        if parsed["status"] != 0 or parsed["used_index"] != 1:
            raise NativeTaskChannelError(f"native_catalog_query_failed:{parsed}")
        self._emit(
            "kernel", "catalog_candidates", "Guest selected workspace candidates",
            agent_id=host_agent_id, task_id=task_id,
            records=parsed["records"], candidates=parsed["candidates"],
            used_index=parsed["used_index"], watch_events=parsed["watch_events"],
        )
        return parsed

    def catalog_stale(self, *, host_agent_id: int, task_id: int) -> dict[str, object]:
        parsed = self._catalog_request(
            12,
            f"CATALOG_STALE {host_agent_id} {task_id}",
            host_agent_id=host_agent_id,
            task_id=task_id,
        )
        if parsed["status"] != 0:
            raise NativeTaskChannelError(f"native_catalog_stale_failed:{parsed}")
        self._emit(
            "kernel", "catalog_stale", "Guest invalidated its Catalog window",
            agent_id=host_agent_id, task_id=task_id,
            records=parsed["records"], watch_events=parsed["watch_events"],
        )
        return parsed

    def fence(self, request_id: int) -> dict[str, object]:
        if type(request_id) is not int or request_id <= 0:
            raise NativeTaskChannelError("native_fence_request_invalid")
        challenge = secrets.token_bytes(32)
        line = self._request(
            f"FENCE {request_id} {challenge.hex()}",
            "AGENT_HARNESS FENCE",
            timeout=30.0,
        )
        fields = line.split()
        if len(fields) != 16:
            raise NativeTaskChannelError("native_fence_response_malformed")
        credit_digest, evidence_root = fields[14:16]
        if not all(
            re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in (credit_digest, evidence_root)
        ):
            raise NativeTaskChannelError("native_fence_digest_malformed")
        receipt: dict[str, object] = {
            "syscall_status": int(fields[2]),
            "status": int(fields[3]),
            "flags": int(fields[4]),
            "lifecycle_id": int(fields[5]),
            "lifecycle_generation": int(fields[6]),
            "request_id": int(fields[7]),
            "fence_sequence": int(fields[8]),
            "metadata_generation": int(fields[9]),
            "credit_epoch": int(fields[10]),
            "evidence_first_sequence": int(fields[11]),
            "evidence_last_sequence": int(fields[12]),
            "evidence_event_count": int(fields[13]),
            "credit_digest": credit_digest,
            "evidence_root": evidence_root,
            "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
        }
        if (
            receipt["syscall_status"] != 0
            or receipt["status"] != 0
            or receipt["lifecycle_id"] != self.lifecycle[0]
            or receipt["lifecycle_generation"] != self.lifecycle[1]
            or receipt["request_id"] != request_id
            or receipt["fence_sequence"] <= 0
            or receipt["evidence_event_count"] <= 0
            or evidence_root == "0" * 64
        ):
            raise NativeTaskChannelError(f"native_fence_failed:{receipt}")
        self._emit(
            "kernel", "workflow_fenced", "Guest sealed workflow evidence",
            lifecycle_id=self.lifecycle[0],
            lifecycle_generation=self.lifecycle[1],
            request_id=request_id,
            fence_sequence=receipt["fence_sequence"],
            evidence_event_count=receipt["evidence_event_count"],
            evidence_root=evidence_root,
            credit_digest=credit_digest,
        )
        return receipt

    def delegate(self, descriptor: object) -> None:
        task_id = int(getattr(descriptor, "task_id"))
        target_agent = int(getattr(descriptor, "target_agent"))
        if task_id in self._claimed:
            raise NativeTaskChannelError("native_task_reused")
        self._emit(
            "task", "native_task_submitting",
            f"Submitting Task {task_id} to the native Task Channel",
            task_id=task_id, target_agent=target_agent,
        )
        remaining = max(0.0, float(getattr(descriptor, "deadline_monotonic")) - time.monotonic())
        deadline_tick = self._tick() + max(1, int(remaining * TICKS_PER_SECOND))
        expected_kind = ARTIFACT_KIND_IDS[
            str(getattr(descriptor, "expected_result_kind"))
        ]
        operation_tool = str(getattr(descriptor, "operation_tool"))
        try:
            operation_tool_id = TOOL_IDS[operation_tool]
        except KeyError as error:
            raise NativeTaskChannelError("native_operation_tool_unknown") from error
        command = (
            "DELEGATE "
            f"{task_id} {int(getattr(descriptor, 'correlation_id'))} "
            f"{int(getattr(descriptor, 'parent_task_id'))} "
            f"{int(getattr(descriptor, 'parent_agent'))} {target_agent} "
            f"{int(getattr(descriptor, 'objective_artifact'))} "
            f"{int(getattr(descriptor, 'input_artifact'))} "
            f"{int(getattr(descriptor, 'result_artifact'))} {expected_kind} "
            f"0x{_capability_mask(getattr(descriptor, 'required_capabilities')):x} "
            f"0x{_tool_mask(getattr(descriptor, 'allowed_tools')):x} "
            f"{_revision_digest(str(getattr(descriptor, 'workspace_revision')))} "
            f"{int(getattr(descriptor, 'resource_budget'))} "
            f"{int(getattr(descriptor, 'read_budget'))} {deadline_tick} "
            f"{operation_tool_id}"
        )
        line = self._request(command, "AGENT_HARNESS CLAIM")
        fields = line.split()
        if len(fields) != 5 or int(fields[2]) != task_id or int(fields[4]) != target_agent:
            raise NativeTaskChannelError("native_claim_mismatch")
        self._claimed[task_id] = target_agent
        self._emit(
            "task", "native_task_claimed",
            f"Guest Agent for Host Agent {target_agent} claimed Task {task_id}",
            task_id=task_id, target_agent=target_agent,
        )

    @staticmethod
    def _parse_artifact(line: str, operation: int) -> dict[str, object]:
        fields = line.split()
        if len(fields) != 14 or int(fields[2]) != operation:
            raise NativeTaskChannelError("native_artifact_response_malformed")
        digest = fields[13]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise NativeTaskChannelError("native_artifact_digest_malformed")
        return {
            "operation": int(fields[2]),
            "host_agent_id": int(fields[3]),
            "task_id": int(fields[4]),
            "status": int(fields[5]),
            "handle": int(fields[6]),
            "kind": int(fields[7]),
            "flags": int(fields[8]),
            "length": int(fields[9]),
            "context_sequence": int(fields[10]),
            "producer_agent_id": int(fields[11]),
            "producer_control_id": int(fields[12]),
            "sha256": digest,
        }

    def seal_artifact(
        self,
        *,
        host_agent_id: int,
        task_id: int,
        handle: int,
        kind: str,
        tool: str,
        content: bytes,
        host_context_sequence: int,
        cause_sequence: int = 0,
        shareable: bool = True,
    ) -> dict[str, object]:
        if not content or len(content) > 64 * 1024:
            raise NativeTaskChannelError("native_artifact_size_invalid")
        try:
            kind_id = ARTIFACT_KIND_IDS[kind]
            tool_id = TOOL_IDS[tool]
        except KeyError as error:
            raise NativeTaskChannelError("native_artifact_type_unknown") from error
        flags = ARTIFACT_F_UTF8 | (ARTIFACT_F_SHAREABLE if shareable else 0)
        digest = hashlib.sha256(content).hexdigest()
        common = (
            f"{host_agent_id} {task_id} {handle} {kind_id} {flags} "
            f"{tool_id} {len(content)} {host_context_sequence} "
            f"{cause_sequence} {digest}"
        )
        begin = self._request(
            f"ARTIFACT_BEGIN {common}", "AGENT_HARNESS ARTIFACT"
        )
        parsed = self._parse_artifact(begin, 2)
        if parsed["status"] != 0:
            raise NativeTaskChannelError("native_artifact_begin_failed")
        for offset in range(0, len(content), ARTIFACT_CHUNK_BYTES):
            chunk = content[offset : offset + ARTIFACT_CHUNK_BYTES]
            line = self._request(
                "ARTIFACT_CHUNK "
                f"{host_agent_id} {task_id} {handle} {offset} {chunk.hex()}",
                "AGENT_HARNESS ARTIFACT",
            )
            parsed = self._parse_artifact(line, 3)
            if parsed["status"] != 0 or parsed["length"] != offset + len(chunk):
                raise NativeTaskChannelError("native_artifact_chunk_failed")
        sealed = self._parse_artifact(
            self._request(
                f"ARTIFACT_SEAL {common}", "AGENT_HARNESS ARTIFACT"
            ),
            4,
        )
        if (
            sealed["status"] != 0
            or sealed["handle"] != handle
            or sealed["length"] != len(content)
            or sealed["sha256"] != digest
            or sealed["context_sequence"] <= 0
        ):
            raise NativeTaskChannelError(f"native_artifact_seal_failed:{sealed}")
        self._emit(
            "artifact", "native_artifact_sealed",
            f"Guest sealed Artifact {handle} for Task {task_id}",
            host_agent_id=host_agent_id, task_id=task_id, artifact=handle,
            artifact_kind=kind, bytes=len(content), sha256=digest,
            context_sequence=sealed["context_sequence"],
        )
        return sealed

    def bind_artifact(
        self,
        *,
        host_agent_id: int,
        task_id: int,
        handle: int,
        kind: str,
        tool: str,
        length: int,
        sha256: str,
        host_context_sequence: int,
        cause_sequence: int,
    ) -> dict[str, object]:
        try:
            kind_id = ARTIFACT_KIND_IDS[kind]
            tool_id = TOOL_IDS[tool]
        except KeyError as error:
            raise NativeTaskChannelError("native_artifact_type_unknown") from error
        line = self._request(
            "ARTIFACT_BIND "
            f"{host_agent_id} {task_id} {handle} {kind_id} 0 {tool_id} "
            f"{length} {host_context_sequence} {cause_sequence} {sha256}",
            "AGENT_HARNESS ARTIFACT",
        )
        bound = self._parse_artifact(line, 5)
        if (
            bound["status"] != 0
            or bound["handle"] != handle
            or bound["kind"] != kind_id
            or bound["length"] != length
            or bound["sha256"] != sha256
            or bound["context_sequence"] <= 0
        ):
            raise NativeTaskChannelError("native_artifact_bind_failed")
        self._emit(
            "context", "native_artifact_bound",
            f"Guest Context accepted Artifact {handle}",
            host_agent_id=host_agent_id, task_id=task_id, artifact=handle,
            context_sequence=bound["context_sequence"], cause_sequence=cause_sequence,
        )
        return bound

    def claim(self, task_id: int, agent_id: int) -> None:
        if self._claimed.get(task_id) != agent_id:
            raise NativeTaskChannelError("native_claim_identity_mismatch")

    def request_cancel(self, task_id: int) -> dict[str, int]:
        """Publish a cooperative cancel offer without fabricating completion."""

        if task_id not in self._claimed:
            raise NativeTaskChannelError("native_cancel_task_unknown")
        line = self._request(
            f"CANCEL {task_id}", "AGENT_HARNESS CANCEL"
        )
        fields = line.split()
        if len(fields) != 7 or int(fields[2]) != task_id:
            raise NativeTaskChannelError("native_cancel_malformed")
        result = {
            "status": int(fields[3]),
            "state": int(fields[4]),
            "terminal_status": int(fields[5]),
            "terminal_generation": int(fields[6]),
        }
        if (
            result["status"] != 0
            or result["state"] not in (2, 3)
            or result["terminal_status"] != STATUS_IDS["cancelled"]
            or result["terminal_generation"] <= 0
        ):
            raise NativeTaskChannelError(f"native_cancel_failed:{result}")
        self._emit(
            "task", "native_task_cancel_requested",
            f"Guest published a cancel offer for Task {task_id}",
            task_id=task_id, target_agent=self._claimed[task_id],
            terminal_generation=result["terminal_generation"],
        )
        return result

    def collect_cancel(self, task_id: int) -> dict[str, int]:
        """Wait until the responsive provider has produced the terminal CQE."""

        if task_id not in self._claimed:
            raise NativeTaskChannelError("native_cancel_task_unknown")
        line = self._request(
            f"COLLECT_CANCEL {task_id}", "AGENT_HARNESS CANCELLED"
        )
        fields = line.split()
        if (
            len(fields) != 6
            or int(fields[2]) != task_id
            or int(fields[3]) != STATUS_IDS["cancelled"]
            or int(fields[5]) <= 0
        ):
            raise NativeTaskChannelError("native_cancel_terminal_mismatch")
        self._claimed.pop(task_id, None)
        result = {
            "status": int(fields[3]),
            "flags": int(fields[4]),
            "context_sequence": int(fields[5]),
        }
        self._emit(
            "task", "native_task_cancelled",
            f"Native Task {task_id} reached a confirmed cancelled terminal",
            task_id=task_id, status="cancelled",
            context_sequence=result["context_sequence"],
        )
        return result

    def cancel(self, task_id: int) -> dict[str, int]:
        self.request_cancel(task_id)
        return self.collect_cancel(task_id)

    def complete(
        self, task_id: int, agent_id: int, status: str, result_artifact: int
    ) -> dict[str, int]:
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
        actual_status = int(fields[3]) if len(fields) == 6 else 1
        if (
            len(fields) != 6
            or int(fields[2]) != task_id
            or actual_status not in (
                native_status, STATUS_IDS["timeout"], STATUS_IDS["cancelled"]
            )
        ):
            raise NativeTaskChannelError("native_completion_mismatch")
        self._claimed.pop(task_id, None)
        actual_name = STATUS_NAMES.get(actual_status, f"status_{actual_status}")
        self._emit(
            "task", "native_task_completed",
            f"Native Task {task_id} reached terminal status {actual_name}",
            task_id=task_id, target_agent=agent_id, status=actual_name,
            result_artifact=result_artifact,
        )
        return {
            "status": actual_status,
            "flags": int(fields[4]),
            "context_sequence": int(fields[5]),
        }

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
            self._guest_directory.cleanup()
            self._emit(
                "qemu", "guest_closed", "Persistent Harness Guest closed",
                lifecycle_id=self.lifecycle[0],
                lifecycle_generation=self.lifecycle[1],
            )

    def __enter__(self) -> "NativeTaskChannel":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


__all__ = [
    "NativeTaskChannel",
    "NativeTaskChannelError",
]
