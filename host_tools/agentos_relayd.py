#!/usr/bin/env python3
"""Long-running AgentOS QEMU/model relay daemon.

The daemon owns transport, TLS and terminal fan-out only.  Conversation
history, tool selection and tool execution remain in the Guest Agent loop.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import queue
import secrets
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

# `python -I -S path/to/script.py` intentionally omits the script directory.
# Bootstrap only this audited sibling directory; no site packages are needed.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_local_protocol as local  # noqa: E402
import guest_llm_relay as relay  # noqa: E402


MAX_USER_MESSAGE_BYTES = relay.MAX_GOAL_BYTES
MAX_LOCAL_CLIENTS = 8
MAX_CLIENT_QUEUE = 128
MAX_PEER_INBOUND_QUEUE = 32
MAX_LOCAL_DRAIN = 16
MAX_MODEL_ERROR_MESSAGE_BYTES = 240
SERIAL_WRITE_TIMEOUT_SECONDS = 5.0
APPROVAL_TIMEOUT_SECONDS = 25.0
NEXUS_APPROVAL_TIMEOUT_SECONDS = 90.0
INTERACTIVE_PROVIDER_TIMEOUT_SECONDS = 100.0
SHUTDOWN_GRACE_SECONDS = 5.0
DEFAULT_BOOT_TIMEOUT_SECONDS = 120.0
READY_LINE = relay.GUEST_RELAY_READY_LINE
NEXUS_READY_LINE = b"agentnexus_ucore: relay_ready=1 nexus=1"
NEXUS_MAX_ROUNDS = 16
NEXUS_FEATURES = ("task_event_v1",)
NEXUS_APPROVAL_TOOL_IDS = {"publish_report": 1004}
NEXUS_GUEST_KINDS = relay.WIRE_V2_GUEST_KINDS | frozenset(("TASK_EVENT",))
NEXUS_WIRE_KINDS = relay.WIRE_V2_HOST_KINDS | NEXUS_GUEST_KINDS
TASK_EVENT_REQUIRED_FIELDS = frozenset(
    (
        "turn_id",
        "request_id",
        "corr_id",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
        "task_id",
        "parent_task_id",
        "event",
        "task_state",
        "role",
        "agent_pid",
        "agent_id",
        "control_id_known",
        "status",
        "tick",
    )
)
TASK_EVENT_OPTIONAL_FIELDS = frozenset(
    (
        "control_id",
        "deadline_tick",
        "artifact_handle",
        "context_seq",
        "provenance",
        "metric_code",
        "metric_value",
        "digest",
        "resource_used",
        "source_pid",
        "target_pid",
        "summary",
    )
)
TASK_EVENTS = frozenset(
    (
        "assigned",
        "accepted",
        "progress",
        "completed",
        "failed",
        "cancelled",
        "artifact_published",
    )
)
TASK_STATES = frozenset(
    ("assigned", "accepted", "running", "waiting", "completed", "failed", "cancelled")
)
TASK_ROLES = frozenset(("coordinator", "system", "research", "analyst", "relay"))
KERNEL_AUDIT_REQUIRED_FIELDS = frozenset(
    (
        "source",
        "event",
        "fresh",
        "record_sequence",
        "tick",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
        "pid",
        "agent_id",
        "actor_control_id",
        "role",
        "audit_kind",
        "loop_state",
        "tool_id",
        "event_type",
        "source_pid",
        "target_pid",
        "status",
        "value0",
        "value1",
        "value2",
        "provenance",
    )
)
KERNEL_SNAPSHOT_REQUIRED_FIELDS = frozenset(
    (
        "source",
        "event",
        "fresh",
        "tick",
        "pid",
        "agent_id",
        "actor_control_id",
        "role",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
        "loop_state",
        "capability_mask",
        "context_seq",
        "wait_sleep_delta",
        "wait_wakeup_delta",
        "sched_dispatch",
        "sched_dispatch_count",
        "sched_budget",
        "sched_budget_used",
        "sched_vruntime",
    )
)
KERNEL_SNAPSHOT_OPTIONAL_FIELDS = frozenset()
APPROVAL_BINDING_FIELDS = (
    "turn_id",
    "request_id",
    "corr_id",
    "tool",
    "arguments_sha256",
    "nonce",
)
NEXUS_APPROVAL_BINDING_FIELDS = APPROVAL_BINDING_FIELDS + (
    "tool_id",
    "issued_tick",
    "expires_tick",
)
OBSERVER_TELEMETRY_FIELDS = frozenset(
    (
        "event",
        "session_id",
        "guest_profile",
        "turn_id",
        "request_id",
        "corr_id",
        "tick",
        "pid",
        "control_id",
        "control_id_known",
        "agent_pid",
        "agent_id",
        "agent_control_id",
        "role",
        "agent_role",
        "task_id",
        "parent_task_id",
        "task_state",
        "deadline_tick",
        "artifact_handle",
        "artifact_sha256",
        "audit_kind",
        "event_type",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
        "generation",
        "actor_control_id",
        "metric_code",
        "metric_value",
        "resource_used",
        "source_pid",
        "target_pid",
        "wait_sleep_delta",
        "wait_wakeup_delta",
        "wait_sleep_count",
        "wait_wakeup_count",
        "sched_dispatch",
        "sched_dispatch_count",
        "sched_budget",
        "sched_budget_used",
        "sched_vruntime",
        "capability_mask",
        "state",
        "loop_state",
        "tool",
        "tool_id",
        "status",
        "code",
        "context_seq",
        "sequence",
        "record_sequence",
        "fresh",
        "provenance",
        "labels",
        "value0",
        "value1",
        "value2",
        "round",
        "request_sha256",
        "reason",
        "source",
    )
)


def _positive_u64(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= relay.MAX_SEQUENCE
    ):
        raise relay.WireProtocolError("BAD_REQUEST", f"{label} must be positive u64")
    return value


def _u64(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= relay.MAX_SEQUENCE
    ):
        raise relay.WireProtocolError("BAD_TASK_EVENT", f"{label} must be u64")
    return value


def _u32(value: object, label: str) -> int:
    result = _u64(value, label)
    if result > 0xFFFFFFFF:
        raise relay.WireProtocolError("BAD_TASK_EVENT", f"{label} must be u32")
    return result


def _i32(value: object, label: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else -(1 << 31)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= (1 << 31) - 1
    ):
        raise relay.WireProtocolError("BAD_TASK_EVENT", f"{label} must be i32")
    return value


def _text(value: object, label: str, *, maximum: int, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise relay.WireProtocolError("BAD_REQUEST", f"{label} must be text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise relay.WireProtocolError("BAD_REQUEST", f"{label} is invalid text") from error
    if len(raw) > maximum:
        raise relay.WireProtocolError("BAD_REQUEST", f"{label} is too long")
    return value


def _utf8_prefix(value: str, maximum: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= maximum:
        return value
    return raw[:maximum].decode("utf-8", errors="ignore")


@dataclass
class ActiveTurn:
    turn_id: int
    request_id: int
    generation: int
    rounds: int = 0
    cancelled: bool = False


@dataclass(frozen=True)
class ProviderCompletion:
    generation: int
    turn_id: int
    request_id: int
    corr_id: int
    reply: relay.ModelReply | None
    error: relay.RelayError | None


class InteractiveSession:
    """Protocol-v2 state machine independent of QEMU and local socket plumbing."""

    def __init__(
        self,
        provider: relay.ModelProvider,
        *,
        send_line: Callable[[bytes], None],
        controller_sink: Callable[[Mapping[str, object]], None],
        telemetry_sink: Callable[[Mapping[str, object]], None],
        session_id: str | None = None,
        max_payload: int = relay.PROTOCOL_MAX_PAYLOAD_BYTES,
        max_rounds: int | None = None,
        max_tokens: int = relay.DEFAULT_MAX_OUTPUT_TOKENS,
        guest_profile: str = "agentlive",
    ) -> None:
        if (
            not isinstance(guest_profile, str)
            or guest_profile not in local.GUEST_PROFILES
        ):
            raise ValueError("Guest profile is unsupported")
        profile_max_rounds = (
            NEXUS_MAX_ROUNDS
            if guest_profile == "nexus"
            else relay.DEFAULT_MAX_ROUNDS
        )
        if max_rounds is None:
            max_rounds = profile_max_rounds
        if (
            not isinstance(max_rounds, int)
            or isinstance(max_rounds, bool)
            or not 1 <= max_rounds <= profile_max_rounds
        ):
            raise ValueError("max_rounds is outside Guest profile policy")
        if not 1 <= max_tokens <= relay.MAX_OUTPUT_TOKENS:
            raise ValueError("max_tokens is outside Host policy")
        self.provider = provider
        defer_call_commits = getattr(provider, "defer_call_commits", None)
        if callable(defer_call_commits):
            defer_call_commits()
        self.send_line = send_line
        self.controller_sink = controller_sink
        self.telemetry_sink = telemetry_sink
        self.session_id = session_id or secrets.token_hex(16)
        self.guest_profile = guest_profile
        self.approval_timeout_seconds = (
            NEXUS_APPROVAL_TIMEOUT_SECONDS
            if guest_profile == "nexus"
            else APPROVAL_TIMEOUT_SECONDS
        )
        self.max_tool_arguments = (
            relay.MAX_NEXUS_TOOL_ARGUMENTS
            if guest_profile == "nexus"
            else relay.MAX_TOOL_ARGUMENTS
        )
        wire_kinds = (
            tuple(NEXUS_WIRE_KINDS)
            if guest_profile == "nexus"
            else tuple(relay.WIRE_V2_KINDS)
        )
        self._guest_kinds = (
            NEXUS_GUEST_KINDS
            if guest_profile == "nexus"
            else relay.WIRE_V2_GUEST_KINDS
        )
        self.codec = relay.FrameCodec(
            max_payload,
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=wire_kinds,
        )
        self.rx = relay.ReceiveSequence(self.session_id)
        self.tx_seq = 1
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.ready = False
        self.closed = False
        self.closing = False
        self._close_reason = ""
        self.active: ActiveTurn | None = None
        self.pending_approval: dict[str, object] | None = None
        self._approval_deadline = 0.0
        self.session_approvals: set[object] = set()
        self._next_turn = 1
        self._next_request = 1
        self._generation = 0
        self._last_corr = 0
        self._last_model_response_corr = 0
        self._last_nexus_tool_use: tuple[int, str, str] | None = None
        self._approval_bindings: set[tuple[int, int, int, str, str]] = set()
        self._model_job: tuple[int, int] | None = None
        self._provider_inflight: set[tuple[int, int]] = set()
        self._provider_results: queue.Queue[ProviderCompletion] = queue.Queue()
        self._controls: dict[int, str] = {}
        self._nexus_lifecycle: tuple[int, int] | None = None
        self._kernel_audit_sequence = 0
        self._kernel_identities: dict[int, tuple[str, int, int, int]] = {}

    def start(self) -> None:
        if self.ready:
            raise relay.WireProtocolError("BAD_STATE", "interactive session already started")
        self.ready = True
        hello: dict[str, object] = {
            "protocol": 2,
            "max_payload": self.codec.max_payload_bytes,
            "max_rounds": self.max_rounds,
            "max_tokens": self.max_tokens,
        }
        if self.guest_profile == "nexus":
            hello.update(
                {"guest_profile": "nexus", "features": list(NEXUS_FEATURES)}
            )
        self._send("HELLO", hello)
        self._controller(
            {
                "type": "session_ready",
                "session_id": self.session_id,
                "max_rounds": self.max_rounds,
                "guest_profile": self.guest_profile,
            }
        )
        self._telemetry(
            {
                "event": "session_ready",
                "session_id": self.session_id,
                "guest_profile": self.guest_profile,
            }
        )

    def submit_user(self, content: object) -> tuple[int, int]:
        self._require_open()
        if self.closing:
            raise relay.WireProtocolError("SESSION_CLOSING", "interactive session is closing")
        if not self.ready:
            raise relay.WireProtocolError("NOT_READY", "Guest is still booting")
        if self.active is not None:
            raise relay.WireProtocolError("TURN_ACTIVE", "a user turn is already active")
        if self.guest_profile == "nexus" and self._provider_inflight:
            raise relay.WireProtocolError(
                "PROVIDER_BUSY", "a cancelled provider request is still completing"
            )
        message = _text(content, "user content", maximum=MAX_USER_MESSAGE_BYTES)
        turn_id = self._next_turn
        self._next_turn += 1
        request_id = self._allocate_request()
        self._generation += 1
        self.active = ActiveTurn(turn_id, request_id, self._generation)
        self._last_model_response_corr = 0
        self._last_nexus_tool_use = None
        self._approval_bindings.clear()
        self._send(
            "USER_MESSAGE",
            {"turn_id": turn_id, "request_id": request_id, "content": message},
        )
        event = {"type": "turn_started", "turn_id": turn_id, "request_id": request_id}
        self._controller(event)
        self._telemetry({"event": "turn_started", **event})
        return turn_id, request_id

    def request_control(self, command: object) -> int:
        self._require_open()
        name = _text(command, "control command", maximum=16)
        commands = {"tools", "context", "status", "reset"}
        if self.guest_profile == "nexus":
            commands.update(("agents", "tasks", "artifacts"))
        if name not in commands:
            raise relay.WireProtocolError("BAD_COMMAND", "unsupported control command")
        if self.active is not None:
            raise relay.WireProtocolError(
                "TURN_ACTIVE", "Guest control commands require an idle session"
            )
        if (
            self.guest_profile == "nexus"
            and name == "reset"
            and self._provider_inflight
        ):
            raise relay.WireProtocolError(
                "PROVIDER_BUSY", "the provider must finish before session reset"
            )
        request_id = self._allocate_request()
        self._controls[request_id] = name
        self._send("CONTROL_REQUEST", {"request_id": request_id, "command": name})
        return request_id

    def cancel(self) -> bool:
        self._require_open()
        turn = self.active
        if turn is None:
            return False
        if turn.cancelled:
            return True
        turn.cancelled = True
        self._generation += 1
        self._model_job = None
        self.pending_approval = None
        self._approval_deadline = 0.0
        self._last_model_response_corr = 0
        self._last_nexus_tool_use = None
        self._send(
            "CANCEL",
            {
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "reason": "user_interrupt",
            },
        )
        self._controller(
            {"type": "turn_cancelling", "turn_id": turn.turn_id, "request_id": turn.request_id}
        )
        self._telemetry(
            {
                "event": "turn_cancel",
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
            }
        )
        return True

    def decide_approval(
        self, decision: object, binding: Mapping[str, object]
    ) -> None:
        self._require_open()
        choice = _text(decision, "approval decision", maximum=16)
        if choice not in ("once", "session", "deny"):
            raise relay.WireProtocolError("BAD_APPROVAL", "approval must be once, session or deny")
        pending = self.pending_approval
        if pending is None:
            raise relay.WireProtocolError("NO_APPROVAL", "there is no pending approval")
        if time.monotonic() >= self._approval_deadline:
            self._deny_pending_approval(pending, "approval_timeout")
            raise relay.WireProtocolError(
                "APPROVAL_TIMEOUT", "approval expired and was denied"
            )
        binding_fields = (
            NEXUS_APPROVAL_BINDING_FIELDS
            if self.guest_profile == "nexus"
            else APPROVAL_BINDING_FIELDS
        )
        if set(binding) != set(binding_fields):
            raise relay.WireProtocolError("BAD_APPROVAL", "approval binding is malformed")
        for key in ("turn_id", "request_id", "corr_id"):
            if _positive_u64(binding.get(key), key) != pending[key]:
                raise relay.WireProtocolError("STALE_APPROVAL", "approval binding is stale")
        tool = _text(binding.get("tool"), "approval tool", maximum=64)
        digest = _text(
            binding.get("arguments_sha256"), "argument digest", maximum=64
        )
        nonce = _text(binding.get("nonce"), "approval nonce", maximum=128)
        if (
            tool != pending["tool"]
            or not hmac.compare_digest(digest, str(pending["arguments_sha256"]))
            or not hmac.compare_digest(nonce, str(pending["nonce"]))
        ):
            raise relay.WireProtocolError("STALE_APPROVAL", "approval binding is stale")
        if self.guest_profile == "nexus":
            tool_id = _positive_u64(binding.get("tool_id"), "tool_id")
            issued_tick = relay._require_u64(binding.get("issued_tick"), "issued_tick")
            expires_tick = relay._require_u64(binding.get("expires_tick"), "expires_tick")
            if (
                tool_id != pending["tool_id"]
                or issued_tick != pending["issued_tick"]
                or expires_tick != pending["expires_tick"]
            ):
                raise relay.WireProtocolError(
                    "STALE_APPROVAL", "approval binding is stale"
                )
        if choice == "session":
            self.session_approvals.add(self._session_approval_key(pending))
        self._send_approval(pending, choice)

    def _session_approval_key(self, binding: Mapping[str, object]) -> object:
        if self.guest_profile != "nexus":
            return str(binding["tool"])
        tool_id = binding.get("tool_id")
        if not isinstance(tool_id, int) or isinstance(tool_id, bool) or tool_id <= 0:
            raise relay.WireProtocolError(
                "BAD_APPROVAL", "Nexus approval requires a positive tool_id"
            )
        digest = str(binding["arguments_sha256"])
        return ("nexus", str(binding["tool"]), tool_id, digest)

    def poll_approval(self, *, controller_available: bool) -> bool:
        """Fail closed when no human can answer or the wall deadline expires."""

        pending = self.pending_approval
        if pending is None:
            return False
        if controller_available and time.monotonic() < self._approval_deadline:
            return False
        reason = "controller_unavailable" if not controller_available else "approval_timeout"
        self._deny_pending_approval(pending, reason)
        return True

    def _deny_pending_approval(
        self, pending: Mapping[str, object], reason: str
    ) -> None:
        self._send_approval(pending, "deny")
        self._telemetry(
            {
                "event": reason,
                "turn_id": pending["turn_id"],
                "request_id": pending["request_id"],
                "corr_id": pending["corr_id"],
                "tool": pending["tool"],
                "status": "deny",
            }
        )

    def close(self, reason: str = "user_requested") -> None:
        if self.closed or self.closing:
            return
        self.closing = True
        self._close_reason = reason
        self._generation += 1
        self._model_job = None
        self.pending_approval = None
        self._approval_deadline = 0.0
        self._controller({"type": "session_closing", "reason": reason})
        self._telemetry({"event": "session_closing", "reason": reason})
        if self.active is not None:
            self.cancel()
        else:
            self._send("SESSION_CLOSE", {"reason": reason})

    def handle_line(self, line: bytes) -> None:
        self._require_open()
        frame = self.codec.decode(line)
        self.rx.accept(frame)
        if frame.kind not in self._guest_kinds:
            raise relay.WireProtocolError("BAD_DIRECTION", "Guest sent a Host-only frame")
        payload = frame.json_object()
        handler = {
            "MODEL_REQUEST": self._model_request,
            "APPROVAL_REQUEST": self._approval_request,
            "TOOL_EVENT": self._tool_event,
            "TURN_COMPLETE": self._turn_complete,
            "CONTROL_RESULT": self._control_result,
            "TELEMETRY": self._guest_telemetry,
            "SESSION_CLOSED": self._session_closed,
            **({"TASK_EVENT": self._task_event} if self.guest_profile == "nexus" else {}),
        }[frame.kind]
        handler(payload)

    def poll_provider(self) -> int:
        handled = 0
        while True:
            try:
                completion = self._provider_results.get_nowait()
            except queue.Empty:
                return handled
            handled += 1
            self._provider_inflight.discard(
                (completion.generation, completion.corr_id)
            )
            turn = self.active
            expected = self._model_job
            if (
                turn is None
                or completion.generation != turn.generation
                or expected != (completion.generation, completion.corr_id)
                or turn.cancelled
                or self.closing
            ):
                self._telemetry(
                    {
                        "event": "late_model_result_dropped",
                        "turn_id": completion.turn_id,
                        "request_id": completion.request_id,
                        "corr_id": completion.corr_id,
                    }
                )
                continue
            self._model_job = None
            envelope = {
                "turn_id": completion.turn_id,
                "request_id": completion.request_id,
                "corr_id": completion.corr_id,
            }
            if completion.error is not None:
                self._send_model_error(envelope, completion.error)
                continue
            assert completion.reply is not None
            try:
                response = completion.reply.wire_payload(
                    completion.corr_id,
                    max_tool_arguments=self.max_tool_arguments,
                )
                response_payload = {**envelope, **response}
                # Preflight local validation and the negotiated Guest payload
                # budget before consuming a TX sequence or touching serial.
                response_line = self.codec.encode_json(
                    self.session_id,
                    self.tx_seq,
                    "MODEL_RESPONSE",
                    response_payload,
                )
            except relay.RelayError as caught:
                error = (
                    caught
                    if isinstance(caught, relay.ProviderError)
                    else relay.ProviderError(
                        "MODEL_RESPONSE_INVALID",
                        "provider reply exceeds the negotiated Guest response contract",
                    )
                )
                self._send_model_error(envelope, error)
                continue
            self.tx_seq += 1
            self.send_line(response_line)
            commit_model_reply = getattr(
                self.provider, "commit_model_reply", None
            )
            if callable(commit_model_reply):
                commit_model_reply(completion.corr_id, completion.reply)
            self._last_model_response_corr = completion.corr_id
            self._last_nexus_tool_use = None
            if self.guest_profile == "nexus" and completion.reply.type == "tool_use":
                self._last_nexus_tool_use = (
                    completion.corr_id,
                    str(completion.reply.tool),
                    relay.canonical_json_bytes(response["arguments"]).decode("utf-8"),
                )
            public: dict[str, object] = {"type": "model_response", **envelope}
            if completion.reply.type == "tool_use":
                public.update(
                    {
                        "response_type": "tool_use",
                        "tool": completion.reply.tool,
                        "arguments": response["arguments"],
                    }
                )
            else:
                public.update(
                    {"response_type": "final", "content": completion.reply.content}
                )
            self._controller(public)
            self._telemetry(
                {
                    "event": "model_response",
                    **envelope,
                    "status": completion.reply.type,
                    **({"tool": completion.reply.tool} if completion.reply.tool else {}),
                }
            )

    def _send_model_error(
        self, envelope: Mapping[str, object], error: relay.RelayError
    ) -> None:
        self._send(
            "MODEL_ERROR",
            {
                **envelope,
                "type": "error",
                "code": error.code,
                "message": _utf8_prefix(
                    error.public_message, MAX_MODEL_ERROR_MESSAGE_BYTES
                ),
                "retryable": error.retryable,
            },
        )
        self._controller({"type": "model_error", **envelope, "code": error.code})
        self._telemetry({"event": "model_error", **envelope, "status": error.code})

    def status(self) -> dict[str, object]:
        active = self.active
        return {
            "type": "daemon_status",
            "session_id": self.session_id,
            "guest_profile": self.guest_profile,
            "ready": self.ready,
            "closing": self.closing,
            "active_turn": active.turn_id if active else 0,
            "request_id": active.request_id if active else 0,
            "round": active.rounds if active else 0,
            "waiting_model": self._model_job is not None,
            "waiting_approval": self.pending_approval is not None,
        }

    def _model_request(self, payload: dict[str, object]) -> None:
        turn = self._match_active(payload)
        corr_id = _positive_u64(payload.get("corr_id"), "corr_id")
        if corr_id <= self._last_corr:
            raise relay.WireProtocolError(
                "BAD_CORRELATION", "corr_id must increase across the whole session"
            )
        if self._model_job is not None:
            raise relay.WireProtocolError("MODEL_BUSY", "another model request is active")
        if self.pending_approval is not None:
            raise relay.WireProtocolError(
                "APPROVAL_BUSY", "a model request cannot bypass pending approval"
            )
        request_payload = dict(payload)
        del request_payload["turn_id"]
        del request_payload["request_id"]
        request = relay.validate_guest_request(
            request_payload,
            max_output_tokens=self.max_tokens,
            max_tool_arguments=self.max_tool_arguments,
        )
        request_sha256 = hashlib.sha256(relay.canonical_json_bytes(request)).hexdigest()
        self._last_corr = corr_id
        # Only a response to this newer request may authorize a later tool.
        # An approval for the preceding response is stale from this point on.
        self._last_model_response_corr = 0
        self._last_nexus_tool_use = None
        if turn.cancelled:
            # CANCEL is already queued on the Host->Guest direction.  A Guest
            # that published its request just before observing that frame must
            # not make the Host fail-stop or start a provider call.  Consume
            # the valid request and wait for the Guest's cancelled completion.
            self._controller(
                {
                    "type": "model_request_dropped",
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "corr_id": corr_id,
                    "reason": "turn_cancelled",
                    "request_sha256": request_sha256,
                }
            )
            self._telemetry(
                {
                    "event": "model_request_after_cancel_dropped",
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "corr_id": corr_id,
                    "state": "CANCELLING",
                    "request_sha256": request_sha256,
                }
            )
            return
        if self.guest_profile == "nexus" and self._provider_inflight:
            raise relay.WireProtocolError(
                "PROVIDER_BUSY", "a cancelled provider request is still completing"
            )
        if turn.rounds >= self.max_rounds:
            raise relay.WireProtocolError("ROUND_LIMIT", "model round limit is exhausted")
        turn.rounds += 1
        generation = turn.generation
        self._model_job = (generation, corr_id)
        provider_token = (generation, corr_id)
        self._provider_inflight.add(provider_token)
        self._controller(
            {
                "type": "model_request",
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "corr_id": corr_id,
                "round": turn.rounds,
                "request_sha256": request_sha256,
            }
        )
        self._telemetry(
            {
                "event": "llm_request",
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "corr_id": corr_id,
                "state": "WAITING_LLM",
                "request_sha256": request_sha256,
            }
        )

        def worker() -> None:
            reply: relay.ModelReply | None = None
            error: relay.RelayError | None = None
            try:
                reply = self.provider.complete(
                    request,
                    deadline_monotonic=(
                        time.monotonic() + INTERACTIVE_PROVIDER_TIMEOUT_SECONDS
                    ),
                )
                if not isinstance(reply, relay.ModelReply):
                    raise relay.ProviderError(
                        "BAD_PROVIDER_RESPONSE", "provider returned an invalid reply"
                    )
            except relay.RelayError as caught:
                error = caught
            except BaseException:
                error = relay.ProviderError(
                    "PROVIDER_FAILURE", "provider adapter failed unexpectedly"
                )
            self._provider_results.put(
                ProviderCompletion(
                    generation,
                    turn.turn_id,
                    turn.request_id,
                    corr_id,
                    reply,
                    error,
                )
            )

        try:
            threading.Thread(
                target=worker,
                name=f"agentos-model-{corr_id}",
                daemon=True,
            ).start()
        except RuntimeError as error:
            self._model_job = None
            self._provider_inflight.discard(provider_token)
            raise relay.RelayError(
                "PROVIDER_WORKER", "provider request worker could not start"
            ) from error

    def _approval_request(self, payload: dict[str, object]) -> None:
        turn = self._match_active(payload)
        corr_id = _positive_u64(payload.get("corr_id"), "corr_id")
        tool = _text(payload.get("tool"), "approval tool", maximum=64)
        digest = _text(payload.get("arguments_sha256"), "argument digest", maximum=64)
        if relay.WIRE_DIGEST_RE.fullmatch(digest) is None:
            raise relay.WireProtocolError("BAD_APPROVAL", "argument digest is malformed")
        nonce = _text(payload.get("nonce"), "approval nonce", maximum=128)
        approval_key = (turn.turn_id, turn.request_id, corr_id, digest, nonce)
        if approval_key in self._approval_bindings:
            raise relay.WireProtocolError("BAD_APPROVAL", "approval request was replayed")
        self._approval_bindings.add(approval_key)
        if self.pending_approval is not None:
            raise relay.WireProtocolError("APPROVAL_BUSY", "another approval is pending")
        binding: dict[str, object] = {
            "turn_id": turn.turn_id,
            "request_id": turn.request_id,
            "corr_id": corr_id,
            "tool": tool,
            "arguments_sha256": digest,
            "nonce": nonce,
        }
        if self.guest_profile == "nexus":
            missing = {
                key
                for key in ("tool_id", "issued_tick", "expires_tick")
                if key not in payload
            }
            if missing:
                raise relay.WireProtocolError(
                    "BAD_APPROVAL",
                    "Nexus approval is missing its tool or tick binding",
                )
            binding["tool_id"] = _positive_u64(payload["tool_id"], "tool_id")
            expected_tool_id = NEXUS_APPROVAL_TOOL_IDS.get(tool)
            if binding["tool_id"] != expected_tool_id:
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "Nexus approval tool identity is malformed"
                )
        elif "tool_id" in payload:
            binding["tool_id"] = _positive_u64(payload["tool_id"], "tool_id")
        if self.guest_profile == "nexus":
            if "arguments" not in payload or "canonical_arguments" not in payload:
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "Nexus approval arguments are missing"
                )
            arguments = payload["arguments"]
            if (
                not isinstance(arguments, dict)
                or set(arguments) != {"handle"}
                or not isinstance(arguments.get("handle"), int)
                or isinstance(arguments.get("handle"), bool)
                or not 1 <= int(arguments["handle"]) <= 0xFFFFFFFF
            ):
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "Nexus approval arguments are malformed"
                )
            binding["arguments"] = dict(arguments)
            canonical = _text(
                payload["canonical_arguments"],
                "canonical arguments",
                maximum=1024,
                empty=False,
            )
            expected_canonical = relay.canonical_json_bytes(arguments).decode("utf-8")
            if not hmac.compare_digest(canonical, expected_canonical):
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "canonical arguments do not match arguments"
                )
            if not hmac.compare_digest(
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(), digest
            ):
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "canonical arguments do not match their digest"
                )
            binding["canonical_arguments"] = canonical
        elif "arguments" in payload:
            if not isinstance(payload["arguments"], dict):
                raise relay.WireProtocolError("BAD_APPROVAL", "approval arguments are malformed")
            binding["arguments"] = payload["arguments"]
        if self.guest_profile != "nexus" and "canonical_arguments" in payload:
            canonical = _text(
                payload["canonical_arguments"],
                "canonical arguments",
                maximum=1024,
                empty=True,
            )
            if not hmac.compare_digest(
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(), digest
            ):
                raise relay.WireProtocolError(
                    "BAD_APPROVAL", "canonical arguments do not match their digest"
                )
            binding["canonical_arguments"] = canonical
        if "display" in payload:
            binding["display"] = _text(
                payload["display"], "approval display", maximum=1024, empty=True
            )
        issued = payload.get("issued_tick")
        expires = (
            payload.get("expires_tick")
            if self.guest_profile == "nexus"
            else payload.get("expires_tick", payload.get("expiry_tick"))
        )
        if issued is not None:
            binding["issued_tick"] = relay._require_u64(issued, "issued_tick")
        if expires is not None:
            binding["expires_tick"] = relay._require_u64(expires, "expires_tick")
        if issued is not None and expires is not None and int(expires) <= int(issued):
            raise relay.WireProtocolError("BAD_APPROVAL", "approval expiry is not in the future")
        if turn.cancelled:
            # CANCEL is already ordered on the Host->Guest wire.  Do not append
            # a denial behind it: the Guest consumes CANCEL while waiting for
            # approval, and a trailing decision would poison the next turn.
            self._telemetry(
                {
                    "event": "approval_after_cancel_ignored",
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "corr_id": corr_id,
                    "tool": tool,
                }
            )
            return
        if corr_id != self._last_model_response_corr:
            raise relay.WireProtocolError(
                "BAD_APPROVAL", "approval is not bound to the latest model response"
            )
        if (
            self.guest_profile == "nexus"
            and self._last_nexus_tool_use != (corr_id, tool, binding["canonical_arguments"])
        ):
            raise relay.WireProtocolError(
                "BAD_APPROVAL",
                "Nexus approval does not match the latest model tool use",
            )
        approval_policy_key = self._session_approval_key(binding)
        if approval_policy_key in self.session_approvals:
            self.pending_approval = binding
            self._approval_deadline = time.monotonic() + self.approval_timeout_seconds
            self._send_approval(binding, "session")
            return
        self.pending_approval = binding
        self._approval_deadline = time.monotonic() + self.approval_timeout_seconds
        event = {"type": "approval_request", **binding}
        self._controller(event)
        self._telemetry(
            {
                "event": "approval_request",
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "corr_id": corr_id,
                "tool": tool,
            }
        )

    def _send_approval(self, binding: Mapping[str, object], decision: str) -> None:
        binding_fields = (
            NEXUS_APPROVAL_BINDING_FIELDS
            if self.guest_profile == "nexus"
            else APPROVAL_BINDING_FIELDS
        )
        payload = {key: binding[key] for key in binding_fields}
        payload["decision"] = decision
        self._send("APPROVAL_DECISION", payload)
        self.pending_approval = None
        self._approval_deadline = 0.0
        if self.guest_profile == "nexus":
            self._last_model_response_corr = 0
            self._last_nexus_tool_use = None
        self._controller({"type": "approval_decision", **payload})
        self._telemetry(
            {
                "event": "approval_decision",
                "turn_id": payload["turn_id"],
                "request_id": payload["request_id"],
                "corr_id": payload["corr_id"],
                "tool": payload["tool"],
                "status": decision,
            }
        )

    def _tool_event(self, payload: dict[str, object]) -> None:
        if "turn_id" in payload:
            self._match_active(payload)
        event = {**payload, "type": "tool_event"}
        self._controller(event)
        telemetry = dict(payload)
        telemetry.setdefault("event", "tool_event")
        self._telemetry(telemetry, source="guest")

    def _task_event(self, payload: dict[str, object]) -> None:
        fields = set(payload)
        missing = TASK_EVENT_REQUIRED_FIELDS.difference(fields)
        unknown = fields.difference(
            TASK_EVENT_REQUIRED_FIELDS | TASK_EVENT_OPTIONAL_FIELDS
        )
        if missing or unknown:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "Nexus task event fields are malformed"
            )
        self._match_active(payload)
        value = dict(payload)
        lifecycle = (
            _positive_u64(
                payload["workflow_lifecycle_id"], "workflow_lifecycle_id"
            ),
            _positive_u64(
                payload["workflow_lifecycle_generation"],
                "workflow_lifecycle_generation",
            ),
        )
        value["workflow_lifecycle_id"] = lifecycle[0]
        value["workflow_lifecycle_generation"] = lifecycle[1]
        value["corr_id"] = _u64(payload["corr_id"], "corr_id")
        value["task_id"] = _u32(payload["task_id"], "task_id")
        if value["task_id"] == 0:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "task_id must be positive u32"
            )
        value["parent_task_id"] = _u32(
            payload["parent_task_id"], "parent_task_id"
        )
        event = _text(payload["event"], "task event", maximum=32)
        if event not in TASK_EVENTS:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "Nexus task event kind is unsupported"
            )
        task_state = _text(payload["task_state"], "task state", maximum=16)
        if task_state not in TASK_STATES:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "Nexus task state is unsupported"
            )
        role = _text(payload["role"], "Agent role", maximum=24)
        if role not in TASK_ROLES:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "Nexus Agent role is unsupported"
            )
        value["agent_pid"] = _i32(payload["agent_pid"], "agent_pid", positive=True)
        value["agent_id"] = _i32(payload["agent_id"], "agent_id", positive=True)
        value["status"] = _i32(payload["status"], "status")
        value["tick"] = _u64(payload["tick"], "tick")
        known = payload["control_id_known"]
        if not isinstance(known, bool):
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "control_id_known must be boolean"
            )
        control_id = payload.get("control_id")
        if known:
            value["control_id"] = _positive_u64(control_id, "control_id")
            value["agent_control_id"] = value["control_id"]
        elif control_id is not None:
            raise relay.WireProtocolError(
                "BAD_TASK_EVENT", "unknown control identity must be omitted"
            )
        for key in ("deadline_tick", "artifact_handle", "metric_code", "metric_value"):
            if key in payload:
                value[key] = _u32(payload[key], key)
        for key in ("context_seq", "provenance", "resource_used"):
            if key in payload:
                value[key] = _u64(payload[key], key)
        for key in ("source_pid", "target_pid"):
            if key in payload:
                value[key] = _i32(payload[key], key, positive=True)
        if "digest" in payload:
            digest = _text(payload["digest"], "artifact digest", maximum=64)
            if relay.WIRE_DIGEST_RE.fullmatch(digest) is None:
                raise relay.WireProtocolError(
                    "BAD_TASK_EVENT", "artifact digest is malformed"
                )
            value["artifact_sha256"] = digest
        if "summary" in payload:
            summary = _text(
                payload["summary"], "task summary", maximum=256, empty=True
            )
            if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in summary):
                raise relay.WireProtocolError(
                    "BAD_TASK_EVENT", "task summary contains terminal controls"
                )
            value["summary"] = summary
        self._bind_nexus_lifecycle(lifecycle, code="BAD_TASK_EVENT")
        value["agent_role"] = role
        value["task_state"] = task_state
        value["event"] = event
        self._controller({"type": "task_event", **value})
        self._telemetry(value, source="guest")

    def _turn_complete(self, payload: dict[str, object]) -> None:
        turn = self._match_active(payload)
        status = _text(payload.get("status", "completed"), "turn status", maximum=32)
        if status not in ("completed", "cancelled", "error"):
            raise relay.WireProtocolError("BAD_TURN", "unsupported turn completion status")
        answer = payload.get("answer", payload.get("content", ""))
        if answer != "":
            answer = _text(answer, "turn answer", maximum=2048)
        self._generation += 1
        self._model_job = None
        self.pending_approval = None
        self._approval_deadline = 0.0
        event: dict[str, object] = {
            "type": "turn_complete",
            "turn_id": turn.turn_id,
            "request_id": turn.request_id,
            "status": status,
        }
        if answer:
            event["answer"] = answer
        for optional in ("rounds", "context_seq"):
            if optional in payload:
                event[optional] = payload[optional]
        self.active = None
        self._last_model_response_corr = 0
        self._last_nexus_tool_use = None
        self._approval_bindings.clear()
        self._controller(event)
        self._telemetry({"event": "turn_complete", **event})
        if self.closing:
            self._send("SESSION_CLOSE", {"reason": self._close_reason or "user_requested"})

    def _control_result(self, payload: dict[str, object]) -> None:
        request_id = _positive_u64(payload.get("request_id"), "request_id")
        command = self._controls.pop(request_id, None)
        if command is None:
            raise relay.WireProtocolError("BAD_CONTROL", "unknown control request id")
        if payload.get("command", command) != command:
            raise relay.WireProtocolError("BAD_CONTROL", "control command does not match")
        status = payload.get("status", "ok")
        if status not in ("ok", "error"):
            raise relay.WireProtocolError("BAD_CONTROL", "control status is malformed")
        event = {"type": "control_result", "request_id": request_id, "command": command}
        for key in ("status", "result", "code", "message"):
            if key in payload:
                event[key] = payload[key]
        event.setdefault("status", status)
        if command == "reset" and status == "ok":
            self.session_approvals.clear()
            reset = getattr(self.provider, "reset_session", None)
            if callable(reset):
                reset()
        self._controller(event)

    def _bind_nexus_lifecycle(
        self, lifecycle: tuple[int, int], *, code: str
    ) -> None:
        if self._nexus_lifecycle is None:
            self._nexus_lifecycle = lifecycle
        elif lifecycle != self._nexus_lifecycle:
            raise relay.WireProtocolError(
                code, "Nexus workflow lifecycle changed within the session"
            )

    def _bind_kernel_identity(
        self, *, role: str, pid: int, agent_id: int, actor_control_id: int
    ) -> None:
        identity = (role, pid, agent_id, actor_control_id)
        previous = self._kernel_identities.setdefault(pid, identity)
        if previous != identity:
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "kernel identity changed for one Agent PID"
            )

    @staticmethod
    def _telemetry_int(
        value: object,
        label: str,
        *,
        minimum: int,
        maximum: int = relay.MAX_SEQUENCE,
    ) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", f"{label} is outside its telemetry range"
            )
        return value

    def _validate_kernel_telemetry(
        self, payload: Mapping[str, object], source: str
    ) -> dict[str, object]:
        fields = set(payload)
        if source == "kernel_audit":
            if fields != set(KERNEL_AUDIT_REQUIRED_FIELDS):
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit fields are malformed"
                )
            if payload.get("event") != "kernel_audit" or payload.get("fresh") is not True:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit envelope is malformed"
                )
            sequence = self._telemetry_int(
                payload.get("record_sequence"), "record_sequence", minimum=1
            )
            if sequence <= self._kernel_audit_sequence:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit sequence is not increasing"
                )
            lifecycle = (
                self._telemetry_int(
                    payload.get("workflow_lifecycle_id"),
                    "workflow_lifecycle_id",
                    minimum=1,
                ),
                self._telemetry_int(
                    payload.get("workflow_lifecycle_generation"),
                    "workflow_lifecycle_generation",
                    minimum=1,
                ),
            )
            self._telemetry_int(payload.get("tick"), "tick", minimum=0)
            pid = self._telemetry_int(
                payload.get("pid"), "pid", minimum=1, maximum=(1 << 31) - 1
            )
            agent_id = self._telemetry_int(
                payload.get("agent_id"),
                "agent_id",
                minimum=1,
                maximum=(1 << 31) - 1,
            )
            actor_control_id = self._telemetry_int(
                payload.get("actor_control_id"), "actor_control_id", minimum=1
            )
            role = _text(payload.get("role"), "kernel audit role", maximum=24)
            if role not in TASK_ROLES:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit role is unsupported"
                )
            audit_kind = self._telemetry_int(
                payload.get("audit_kind"), "audit_kind", minimum=0, maximum=0xFFFFFFFF
            )
            if audit_kind not in (2, 3):
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit kind is unsupported"
                )
            self._telemetry_int(
                payload.get("loop_state"), "loop_state", minimum=0, maximum=0xFFFFFFFF
            )
            self._telemetry_int(
                payload.get("tool_id"), "tool_id", minimum=0, maximum=0xFFFFFFFF
            )
            if payload.get("event_type") != 2 or isinstance(payload.get("event_type"), bool):
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit event type is unsupported"
                )
            source_pid = self._telemetry_int(
                payload.get("source_pid"),
                "source_pid",
                minimum=1,
                maximum=(1 << 31) - 1,
            )
            target_pid = self._telemetry_int(
                payload.get("target_pid"),
                "target_pid",
                minimum=1,
                maximum=(1 << 31) - 1,
            )
            del source_pid
            self._telemetry_int(
                payload.get("status"),
                "status",
                minimum=-(1 << 31),
                maximum=(1 << 31) - 1,
            )
            self._telemetry_int(payload.get("value0"), "value0", minimum=1)
            self._telemetry_int(payload.get("value1"), "value1", minimum=1)
            value2 = self._telemetry_int(payload.get("value2"), "value2", minimum=0)
            if value2 != target_pid:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit target binding is inconsistent"
                )
            provenance = self._telemetry_int(
                payload.get("provenance"), "provenance", minimum=0
            )
            if provenance != 0:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "MESSAGE audit provenance must be zero"
                )
            self._bind_nexus_lifecycle(lifecycle, code="BAD_TELEMETRY")
            self._bind_kernel_identity(
                role=role,
                pid=pid,
                agent_id=agent_id,
                actor_control_id=actor_control_id,
            )
            self._kernel_audit_sequence = sequence
            return dict(payload)

        expected = KERNEL_SNAPSHOT_REQUIRED_FIELDS | KERNEL_SNAPSHOT_OPTIONAL_FIELDS
        if (
            not KERNEL_SNAPSHOT_REQUIRED_FIELDS.issubset(fields)
            or fields.difference(expected)
        ):
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "kernel snapshot fields are malformed"
            )
        if payload.get("event") != "kernel_snapshot" or payload.get("fresh") is not False:
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "kernel snapshot envelope is malformed"
            )
        lifecycle = (
            self._telemetry_int(
                payload.get("workflow_lifecycle_id"),
                "workflow_lifecycle_id",
                minimum=1,
            ),
            self._telemetry_int(
                payload.get("workflow_lifecycle_generation"),
                "workflow_lifecycle_generation",
                minimum=1,
            ),
        )
        self._telemetry_int(payload.get("tick"), "tick", minimum=0)
        pid = self._telemetry_int(
            payload.get("pid"), "pid", minimum=1, maximum=(1 << 31) - 1
        )
        agent_id = self._telemetry_int(
            payload.get("agent_id"),
            "agent_id",
            minimum=1,
            maximum=(1 << 31) - 1,
        )
        actor_control_id = self._telemetry_int(
            payload.get("actor_control_id"), "actor_control_id", minimum=1
        )
        role = _text(payload.get("role"), "kernel snapshot role", maximum=24)
        if role not in TASK_ROLES:
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "kernel snapshot role is unsupported"
            )
        self._telemetry_int(
            payload.get("loop_state"), "loop_state", minimum=0, maximum=0xFFFFFFFF
        )
        self._telemetry_int(
            payload.get("capability_mask"), "capability_mask", minimum=1
        )
        self._telemetry_int(payload.get("context_seq"), "context_seq", minimum=1)
        self._telemetry_int(
            payload.get("wait_sleep_delta"), "wait_sleep_delta", minimum=1
        )
        self._telemetry_int(
            payload.get("wait_wakeup_delta"), "wait_wakeup_delta", minimum=1
        )
        dispatch = self._telemetry_int(
            payload.get("sched_dispatch"), "sched_dispatch", minimum=1
        )
        dispatch_count = self._telemetry_int(
            payload.get("sched_dispatch_count"), "sched_dispatch_count", minimum=0
        )
        if dispatch_count < dispatch:
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "scheduler dispatch counters are inconsistent"
            )
        self._telemetry_int(payload.get("sched_budget"), "sched_budget", minimum=1)
        self._telemetry_int(
            payload.get("sched_budget_used"), "sched_budget_used", minimum=1
        )
        self._telemetry_int(payload.get("sched_vruntime"), "sched_vruntime", minimum=0)
        self._bind_nexus_lifecycle(lifecycle, code="BAD_TELEMETRY")
        self._bind_kernel_identity(
            role=role,
            pid=pid,
            agent_id=agent_id,
            actor_control_id=actor_control_id,
        )
        return dict(payload)

    @staticmethod
    def _sanitize_telemetry_reason(value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            if -(1 << 31) <= value <= (1 << 31) - 1:
                return value
        elif isinstance(value, str):
            reason = _text(value, "telemetry reason", maximum=64, empty=False)
            if not any(
                ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F
                for char in reason
            ):
                return reason
        raise relay.WireProtocolError(
            "BAD_TELEMETRY", "telemetry reason must be a bounded scalar"
        )

    def _guest_telemetry(self, payload: dict[str, object]) -> None:
        declared = payload.get("source")
        source = "guest"
        if declared is not None:
            source = _text(declared, "telemetry source", maximum=32)
            allowed_sources = {
                "guest_policy",
                "context_timeline",
                "context_snapshot",
            }
            if self.guest_profile == "nexus":
                allowed_sources.update(("kernel_audit", "kernel_snapshot"))
            if source not in allowed_sources:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "Guest telemetry source is unsupported"
                )
        if source in ("kernel_audit", "kernel_snapshot"):
            validated = self._validate_kernel_telemetry(payload, source)
            self._telemetry(validated, source=source, guest_origin=True)
            return
        event = _text(
            payload.get("event"), "telemetry event", maximum=64
        )
        fresh = payload.get("fresh")
        if fresh is not None and not isinstance(fresh, bool):
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "telemetry freshness must be boolean"
            )
        record_sequence = payload.get("record_sequence")
        if record_sequence is not None:
            record_sequence = relay._require_u64(
                record_sequence, "record_sequence"
            )
        if source == "context_timeline" and (
            event != "kernel_timeline"
            or fresh is not True
            or not isinstance(record_sequence, int)
            or record_sequence == 0
        ):
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "fresh timeline telemetry is malformed"
            )
        if source == "context_snapshot" and (
            event != "kernel_snapshot"
            or fresh is not False
            or record_sequence != 0
        ):
            raise relay.WireProtocolError(
                "BAD_TELEMETRY", "snapshot telemetry is malformed"
            )
        validated = dict(payload)
        if "reason" in validated:
            validated["reason"] = self._sanitize_telemetry_reason(
                validated["reason"]
            )
        self._telemetry(validated, source=source, guest_origin=True)

    def _session_closed(self, payload: dict[str, object]) -> None:
        if not self.closing:
            raise relay.WireProtocolError("BAD_CLOSE", "Guest closed an active session")
        reason = _text(payload.get("reason", "guest_complete"), "close reason", maximum=64)
        if isinstance(self.provider, relay.ReplayProvider):
            self.provider.assert_exhausted()
        self.closed = True
        self._controller({"type": "session_closed", "reason": reason})
        self._telemetry({"event": "session_closed", "reason": reason})

    def _match_active(self, payload: Mapping[str, object]) -> ActiveTurn:
        turn = self.active
        if turn is None:
            raise relay.WireProtocolError("NO_TURN", "Guest frame has no active user turn")
        if (
            _positive_u64(payload.get("turn_id"), "turn_id") != turn.turn_id
            or _positive_u64(payload.get("request_id"), "request_id") != turn.request_id
        ):
            raise relay.WireProtocolError("BAD_TURN", "Guest frame belongs to another turn")
        return turn

    def _allocate_request(self) -> int:
        request_id = self._next_request
        self._next_request += 1
        return request_id

    def _send(self, kind: str, payload: Mapping[str, object]) -> None:
        line = self.codec.encode_json(self.session_id, self.tx_seq, kind, payload)
        self.tx_seq += 1
        self.send_line(line)

    def _controller(self, payload: Mapping[str, object]) -> None:
        self.controller_sink(dict(payload))

    def _telemetry(
        self,
        payload: Mapping[str, object],
        *,
        source: str = "host",
        guest_origin: bool = False,
    ) -> None:
        # Observer transport is metadata-only.  Tool output and model/user
        # content stay on the authenticated controller channel.
        if source in ("kernel_audit", "kernel_snapshot") and not guest_origin:
            source = "host"
        value: dict[str, object] = {}
        for key in OBSERVER_TELEMETRY_FIELDS:
            if key not in payload:
                continue
            item = payload[key]
            if isinstance(item, bool):
                value[key] = item
            elif isinstance(item, int) and -(1 << 63) <= item <= relay.MAX_SEQUENCE:
                value[key] = item
            elif isinstance(item, str):
                try:
                    encoded = item.encode("utf-8")
                except UnicodeEncodeError:
                    continue
                if len(encoded) > 256 or any(
                    ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F
                    for char in item
                ):
                    continue
                value[key] = item
        value["source"] = source
        self.telemetry_sink({"type": "telemetry", **value})

    def _require_open(self) -> None:
        if self.closed:
            raise relay.WireProtocolError("SESSION_CLOSED", "interactive session is closed")


class _Peer:
    def __init__(
        self,
        connection: socket.socket,
        *,
        role: str,
        inbound: queue.Queue[tuple["_Peer", dict[str, object]]],
        on_close: Callable[["_Peer"], None],
    ) -> None:
        self.connection = connection
        self.role = role
        self.inbound = inbound
        self.on_close = on_close
        self.outbound: queue.Queue[bytes | None] = queue.Queue(MAX_CLIENT_QUEUE)
        self._outbound_condition = threading.Condition()
        self._outbound_pending = 0
        self._inbound_lock = threading.Lock()
        self._inbound_queued = 0
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer = threading.Thread(target=self._write, daemon=True)

    def start(self) -> None:
        self.reader.start()
        self.writer.start()

    def send(self, message: Mapping[str, object]) -> bool:
        try:
            encoded = local.encode_message(message)
        except local.LocalProtocolError:
            self.close()
            return False
        with self._outbound_condition:
            if self.closed.is_set():
                return False
            # Count the item before publishing it to the writer.  This closes
            # the dequeue/sendall window in which Queue.empty() alone would
            # incorrectly report that a final event had settled.
            self._outbound_pending += 1
            try:
                self.outbound.put_nowait(encoded)
            except queue.Full:
                self._outbound_pending -= 1
                self._outbound_condition.notify_all()
            else:
                return True
        self.close()
        return False

    def output_idle(self) -> bool:
        with self._outbound_condition:
            return self._outbound_pending == 0

    def _queue_inbound(self, message: dict[str, object]) -> bool:
        with self._inbound_lock:
            if self.closed.is_set() or self._inbound_queued >= MAX_PEER_INBOUND_QUEUE:
                accepted = False
            else:
                self._inbound_queued += 1
                accepted = True
        if accepted:
            try:
                self.inbound.put_nowait((self, message))
                return True
            except queue.Full:
                with self._inbound_lock:
                    self._inbound_queued -= 1
        # A client that outpaces the bounded daemon control plane is isolated
        # just like a slow outbound observer; it cannot consume relay memory.
        self.close()
        return False

    def inbound_consumed(self) -> None:
        # A few focused tests inject directly into the shared queue.  Treat
        # those as already accounted rather than over-releasing a semaphore.
        with self._inbound_lock:
            if self._inbound_queued:
                self._inbound_queued -= 1

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass
        try:
            self.outbound.put_nowait(None)
        except queue.Full:
            pass
        self.on_close(self)

    def _read(self) -> None:
        reader = local.NdjsonReader()
        try:
            while not self.closed.is_set():
                chunk = self.connection.recv(4096)
                if not chunk:
                    reader.finish()
                    return
                for message in reader.feed(chunk):
                    if not self._queue_inbound(message):
                        return
        except (OSError, local.LocalProtocolError):
            return
        finally:
            self.close()

    def _write(self) -> None:
        try:
            while not self.closed.is_set():
                item = self.outbound.get()
                if item is None:
                    return
                try:
                    self.connection.sendall(item)
                finally:
                    with self._outbound_condition:
                        self._outbound_pending -= 1
                        self._outbound_condition.notify_all()
        except OSError:
            return
        finally:
            self.close()


class _FairInboundQueue:
    """Bounded role queues with round-robin controller/observer service."""

    def __init__(self) -> None:
        self._controller: queue.Queue[tuple[_Peer, dict[str, object]]] = queue.Queue(
            MAX_PEER_INBOUND_QUEUE
        )
        self._observers: queue.Queue[tuple[_Peer, dict[str, object]]] = queue.Queue(
            MAX_PEER_INBOUND_QUEUE * MAX_LOCAL_CLIENTS
        )
        self._get_lock = threading.Lock()
        self._controller_next = True

    def _target(
        self, item: tuple[_Peer, dict[str, object]]
    ) -> queue.Queue[tuple[_Peer, dict[str, object]]]:
        return self._controller if item[0].role == "controller" else self._observers

    def put(
        self,
        item: tuple[_Peer, dict[str, object]],
        block: bool = True,
        timeout: float | None = None,
    ) -> None:
        self._target(item).put(item, block=block, timeout=timeout)

    def put_nowait(self, item: tuple[_Peer, dict[str, object]]) -> None:
        self._target(item).put_nowait(item)

    def get_nowait(self) -> tuple[_Peer, dict[str, object]]:
        with self._get_lock:
            queues = (
                (self._controller, self._observers)
                if self._controller_next
                else (self._observers, self._controller)
            )
            for index, values in enumerate(queues):
                try:
                    item = values.get_nowait()
                except queue.Empty:
                    continue
                # Alternate whenever both roles are active.  When the preferred
                # role is empty, keep it preferred for the next arrival.
                if index == 0:
                    self._controller_next = not self._controller_next
                return item
        raise queue.Empty

    def qsize(self) -> int:
        return self._controller.qsize() + self._observers.qsize()

    def empty(self) -> bool:
        return self._controller.empty() and self._observers.empty()


class LocalEndpoints:
    """Authenticated controller and non-blocking observer fan-out."""

    def __init__(self, paths: local.RuntimePaths, token: str) -> None:
        self.paths = paths
        self.token = token
        self.inbound = _FairInboundQueue()
        self.control_server = local.bind_owner_socket(paths.control_socket, backlog=2)
        self.telemetry_server = local.bind_owner_socket(
            paths.telemetry_socket, backlog=MAX_LOCAL_CLIENTS
        )
        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self._controller: _Peer | None = None
        self._observers: set[_Peer] = set()
        self.controller_initial: Callable[[], Mapping[str, object]] | None = None
        self.observer_initial: Callable[[], Mapping[str, object]] | None = None
        self._acceptors = (
            threading.Thread(
                target=self._accept, args=(self.control_server, "controller"), daemon=True
            ),
            threading.Thread(
                target=self._accept, args=(self.telemetry_server, "observer"), daemon=True
            ),
        )

    def start(self) -> None:
        for thread in self._acceptors:
            thread.start()

    def send_controller(self, message: Mapping[str, object]) -> None:
        with self._lock:
            peer = self._controller
        if peer is not None:
            peer.send(message)

    def broadcast(self, message: Mapping[str, object]) -> None:
        with self._lock:
            peers = tuple(self._observers)
        for peer in peers:
            # A full per-observer queue disconnects only that observer.  It can
            # never block serial/model progress or faster observers.
            peer.send(message)

    def has_controller(self) -> bool:
        with self._lock:
            return self._controller is not None and not self._controller.closed.is_set()

    def is_current(self, peer: _Peer) -> bool:
        """Return whether an inbound item still belongs to an attached peer."""

        if peer.closed.is_set():
            return False
        with self._lock:
            if peer.role == "controller":
                return self._controller is peer and not peer.closed.is_set()
            return peer in self._observers and not peer.closed.is_set()

    def close(self) -> None:
        self.stopping.set()
        for server in (self.control_server, self.telemetry_server):
            try:
                server.close()
            except OSError:
                pass
        with self._lock:
            peers = tuple(self._observers) + ((self._controller,) if self._controller else ())
            self._controller = None
            self._observers.clear()
        for peer in peers:
            peer.close()
        for path in (self.paths.control_socket, self.paths.telemetry_socket):
            path.unlink(missing_ok=True)

    def settle(self, timeout: float = 0.2) -> None:
        """Give fast peers a bounded chance to consume final queued events."""

        deadline = time.monotonic() + timeout
        empty_passes = 0
        while time.monotonic() < deadline:
            with self._lock:
                peers = tuple(self._observers) + (
                    (self._controller,) if self._controller else ()
                )
            if all(peer.output_idle() for peer in peers):
                empty_passes += 1
                if empty_passes >= 2:
                    return
            else:
                empty_passes = 0
            time.sleep(0.005)

    def _accept(self, server: socket.socket, expected_role: str) -> None:
        while not self.stopping.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(2.0)
            try:
                stream = connection.makefile("rb", buffering=0)
                hello = local.recv_one(stream)
                stream.close()
                valid = (
                    set(hello) == {"type", "protocol", "role", "token"}
                    and hello.get("type") == "hello"
                    and hello.get("protocol") == local.LOCAL_PROTOCOL
                    and hello.get("role") == expected_role
                    and isinstance(hello.get("token"), str)
                    and hmac.compare_digest(str(hello["token"]), self.token)
                )
                if not valid:
                    connection.sendall(local.encode_message({"type": "error", "code": "AUTH"}))
                    connection.close()
                    continue
                connection.settimeout(None)
                peer = _Peer(
                    connection,
                    role=expected_role,
                    inbound=self.inbound,
                    on_close=self._remove,
                )
                with self._lock:
                    if expected_role == "controller":
                        if self._controller is not None and not self._controller.closed.is_set():
                            connection.sendall(
                                local.encode_message(
                                    {"type": "error", "code": "CONTROLLER_BUSY"}
                                )
                            )
                            connection.close()
                            continue
                        self._controller = peer
                    elif len(self._observers) >= MAX_LOCAL_CLIENTS:
                        connection.sendall(
                            local.encode_message({"type": "error", "code": "OBSERVER_LIMIT"})
                        )
                        connection.close()
                        continue
                    else:
                        self._observers.add(peer)
                peer.send({"type": "welcome", "role": expected_role})
                if expected_role == "controller" and self.controller_initial is not None:
                    peer.send(self.controller_initial())
                if expected_role == "observer" and self.observer_initial is not None:
                    peer.send(self.observer_initial())
                peer.start()
            except (OSError, local.LocalProtocolError):
                try:
                    connection.close()
                except OSError:
                    pass

    def _remove(self, peer: _Peer) -> None:
        with self._lock:
            if self._controller is peer:
                self._controller = None
            self._observers.discard(peer)


class InteractiveRelayDaemon:
    def __init__(
        self,
        process: relay.QemuSerialProcess,
        provider: relay.ModelProvider,
        *,
        paths: local.RuntimePaths,
        token: str,
        session_id: str,
        provider_name: str,
        model_name: str,
        max_payload: int,
        max_rounds: int | None,
        max_tokens: int,
        boot_timeout: float,
        quiet: bool = False,
        shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
        guest_profile: str = "agentlive",
    ) -> None:
        if not 0 < shutdown_grace_seconds <= 30:
            raise ValueError("shutdown grace must be in (0, 30] seconds")
        if (
            not isinstance(guest_profile, str)
            or guest_profile not in local.GUEST_PROFILES
        ):
            raise ValueError("Guest profile is unsupported")
        self.process = process
        self.paths = paths
        self.token = token
        self.provider_name = provider_name
        self.model_name = model_name
        self.boot_timeout = boot_timeout
        self.quiet = quiet
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.guest_profile = guest_profile
        self.ready_line = (
            NEXUS_READY_LINE if guest_profile == "nexus" else READY_LINE
        )
        self.runtime_lock = local.RuntimeLock.acquire(paths.directory)
        try:
            paths.state_file.unlink(missing_ok=True)
            self.endpoints = LocalEndpoints(paths, token)
        except BaseException:
            self.runtime_lock.close()
            raise
        self.events: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self.stop_requested = threading.Event()
        self.close_requested = threading.Event()
        self._signal_close_started = False
        self._shutdown_deadline = 0.0
        self.session = InteractiveSession(
            provider,
            send_line=self._write_serial,
            controller_sink=self.endpoints.send_controller,
            telemetry_sink=self.endpoints.broadcast,
            session_id=session_id,
            max_payload=max_payload,
            max_rounds=max_rounds,
            max_tokens=max_tokens,
            guest_profile=guest_profile,
        )
        self.endpoints.controller_initial = lambda: {
            "type": "session_ready",
            "session_id": self.session.session_id,
            "max_rounds": self.session.max_rounds,
            "provider": self.provider_name,
            "model": self.model_name,
            "guest_profile": self.guest_profile,
        }
        self.endpoints.observer_initial = lambda: {
            "type": "telemetry",
            "source": "host",
            "event": "observer_attached",
            "state": "IDLE" if self.session.active is None else "RUNNING",
            "turn_id": self.session.active.turn_id if self.session.active else 0,
            "request_id": self.session.active.request_id if self.session.active else 0,
            "session_id": self.session.session_id,
            "guest_profile": self.guest_profile,
        }

    def run(self) -> None:
        self.endpoints.start()
        try:
            proc = self.process.start()
            if proc.stdout is None or proc.stderr is None:
                raise relay.RelayError("QEMU_PIPE_CLOSED", "QEMU serial pipes are missing")
        except BaseException:
            self.endpoints.close()
            self.process.stop()
            self.runtime_lock.close()
            raise
        self._reader(proc.stdout, "stdout")
        self._reader(proc.stderr, "stderr")
        scanner = relay.SerialLineScanner(wire_prefix=relay.WIRE_V2_PREFIX)
        boot_deadline = time.monotonic() + self.boot_timeout
        try:
            while not self.stop_requested.is_set() and not self.session.closed:
                self._consume_close_request()
                if self.stop_requested.is_set():
                    continue
                if self._expire_shutdown_if_needed():
                    continue
                self.session.poll_provider()
                self._drain_local()
                self.session.poll_approval(
                    controller_available=self.endpoints.has_controller()
                )
                try:
                    source, chunk = self.events.get(timeout=0.05)
                except queue.Empty:
                    if not self.session.ready and time.monotonic() >= boot_deadline:
                        raise relay.RelayError("BOOT_TIMEOUT", "Guest relay did not become ready")
                    polled = getattr(proc, "poll", lambda: None)()
                    if polled is not None:
                        raise relay.RelayError("QEMU_EXITED", "QEMU exited before session close")
                    continue
                if source == "eof":
                    polled = getattr(proc, "poll", lambda: None)()
                    if polled is not None:
                        raise relay.RelayError("QEMU_EXITED", "QEMU output closed unexpectedly")
                    continue
                if source == "stderr":
                    if not self.quiet:
                        sys.stderr.buffer.write(chunk)
                        sys.stderr.buffer.flush()
                    continue
                for kind, line in scanner.feed(chunk):
                    if kind == "log":
                        rendered = line[:-2] + b"\n" if line.endswith(b"\r\n") else line
                        if rendered.rstrip(b"\n") == self.ready_line:
                            if not self.session.ready:
                                self.session.start()
                                local.publish_state(
                                    self.paths,
                                    session_id=self.session.session_id,
                                    token=self.token,
                                    pid=os.getpid(),
                                    provider=self.provider_name,
                                    model=self.model_name,
                                    guest_profile=self.guest_profile,
                                )
                        if not self.quiet:
                            sys.stdout.buffer.write(line)
                            sys.stdout.buffer.flush()
                    else:
                        if not self.session.ready:
                            raise relay.WireProtocolError(
                                "FRAME_BEFORE_HELLO", "Guest framed before relay readiness"
                            )
                        self.session.handle_line(line)
        finally:
            if self.session.closed:
                self.endpoints.settle()
            self.endpoints.close()
            self.process.stop()
            try:
                if self.paths.state_file.exists():
                    state = local.load_state(self.paths.state_file)
                    if state.get("session_id") == self.session.session_id:
                        self.paths.state_file.unlink(missing_ok=True)
            except local.LocalProtocolError:
                pass
            self.runtime_lock.close()

    def request_stop(self) -> None:
        """Signal-safe request; the event loop performs all serial I/O."""

        self.close_requested.set()

    def _consume_close_request(self) -> bool:
        if not self.close_requested.is_set():
            return False
        self.close_requested.clear()
        if self._signal_close_started:
            return False
        self._signal_close_started = True
        if not self.session.closed and self.session.ready:
            self.initiate_close("host_shutdown")
        else:
            self.stop_requested.set()
        return True

    def initiate_close(self, reason: str) -> None:
        """Start one bounded close handshake for CLI and signal shutdowns."""

        if self.session.closed:
            return
        if not self.session.ready:
            self.stop_requested.set()
            return
        deadline = time.monotonic() + self.shutdown_grace_seconds
        if not self._shutdown_deadline or deadline < self._shutdown_deadline:
            self._shutdown_deadline = deadline
        # Arm the deadline before serial I/O.  Even a blocked close-frame
        # writer cannot leave this daemon without a shutdown bound.
        self.session.close(reason)

    def _expire_shutdown_if_needed(self) -> bool:
        if self._shutdown_deadline and time.monotonic() >= self._shutdown_deadline:
            self.stop_requested.set()
            return True
        return False

    def _reader(self, stream, source: str) -> None:
        def run() -> None:
            while True:
                try:
                    chunk = stream.read(4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    self.events.put(("eof", source.encode("ascii")))
                    return
                self.events.put((source, chunk))

        threading.Thread(target=run, name=f"agentos-{source}", daemon=True).start()

    def _write_serial(self, line: bytes) -> None:
        relay._write_process_before_deadline(
            self.process,
            line,
            deadline_monotonic=time.monotonic() + SERIAL_WRITE_TIMEOUT_SECONDS,
        )

    def _drain_local(self) -> None:
        # Bound each pass so local traffic cannot defer provider polling or
        # Guest serial processing.  The role-aware queue also prevents a ping
        # flood from placing a controller command behind every observer item.
        for _ in range(MAX_LOCAL_DRAIN):
            try:
                peer, message = self.endpoints.inbound.get_nowait()
            except queue.Empty:
                return
            try:
                # A peer may disconnect after its reader queued commands.
                # Identity and liveness are checked at execution time so a
                # replacement controller never inherits the old queue.
                if not self.endpoints.is_current(peer):
                    continue
                try:
                    self._handle_local(peer, message)
                except relay.RelayError as error:
                    peer.send(
                        {
                            "type": "error",
                            "code": error.code,
                            "message": error.public_message,
                        }
                    )
            finally:
                consumed = getattr(peer, "inbound_consumed", None)
                if consumed is not None:
                    consumed()

    def _handle_local(self, peer: _Peer, message: Mapping[str, object]) -> None:
        message_type = message.get("type")
        if peer.role == "observer":
            if message_type == "ping" and set(message) == {"type"}:
                peer.send({"type": "pong"})
                return
            raise relay.WireProtocolError("ROLE_DENIED", "observer is read-only")
        if message_type == "user_message":
            if set(message) != {"type", "content"}:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "user message is malformed")
            self.session.submit_user(message.get("content"))
        elif message_type == "command":
            if set(message) != {"type", "command"}:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "control message is malformed")
            command = message.get("command")
            self.session.request_control(command)
        elif message_type == "approval":
            binding_fields = (
                NEXUS_APPROVAL_BINDING_FIELDS
                if self.guest_profile == "nexus"
                else APPROVAL_BINDING_FIELDS
            )
            expected = {"type", "decision", *binding_fields}
            if set(message) != expected:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "approval message is malformed")
            self.session.decide_approval(
                message.get("decision"),
                {key: message[key] for key in binding_fields},
            )
        elif message_type == "cancel":
            if set(message) != {"type"}:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "cancel message is malformed")
            if not self.session.cancel():
                peer.send({"type": "idle"})
        elif message_type == "session_close":
            if set(message) != {"type"}:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "close message is malformed")
            self.initiate_close("user_requested")
        elif message_type == "ping":
            if set(message) != {"type"}:
                raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "ping message is malformed")
            peer.send({"type": "pong"})
        else:
            raise relay.WireProtocolError("BAD_LOCAL_MESSAGE", "unsupported controller message")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the persistent AgentOS QEMU relay daemon.")
    parser.add_argument("--provider", choices=("openai", "anthropic", "deepseek", "replay"), required=True)
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    parser.add_argument("--kernel", default="build/kernel")
    parser.add_argument("--image", default="nfs/fs-copy.img")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--api-key-env", default="")
    keys.add_argument("--api-key-file", type=Path)
    parser.add_argument("--anthropic-version", default="2023-06-01")
    parser.add_argument("--replay-file", type=Path)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=relay.DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--max-payload-bytes", type=int, default=relay.PROTOCOL_MAX_PAYLOAD_BYTES)
    parser.add_argument("--http-timeout", type=float, default=relay.DEFAULT_HTTP_TIMEOUT_SECONDS)
    parser.add_argument("--max-http-response-bytes", type=int, default=relay.DEFAULT_MAX_HTTP_RESPONSE_BYTES)
    parser.add_argument("--boot-timeout", type=float, default=DEFAULT_BOOT_TIMEOUT_SECONDS)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument(
        "--guest-profile",
        choices=tuple(sorted(local.GUEST_PROFILES)),
        default="agentlive",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def _provider(args: argparse.Namespace) -> relay.ModelProvider:
    # Reuse the audited provider construction without duplicating key loading,
    # endpoint validation or TLS policy.  The namespace exposes its exact
    # expected fields, while one-shot-only goal fields are intentionally absent.
    if args.provider == "replay":
        if args.replay_file is None:
            raise ValueError("--replay-file is required for replay provider")
        return relay.ReplayProvider.from_jsonl(
            args.replay_file, require_request_digests=True
        )
    return relay._build_provider(args)


def main(argv: Sequence[str] | None = None) -> int:
    daemon: InteractiveRelayDaemon | None = None
    try:
        args = _parser().parse_args(argv)
        provider = _provider(args)
        session_id = secrets.token_hex(16)
        paths = local.prepare_runtime_paths(session_id, base=args.runtime_dir)
        token = secrets.token_hex(32)
        command = relay.build_qemu_command(
            relay._resolve_qemu(args.qemu), kernel=args.kernel, image=args.image
        )
        daemon = InteractiveRelayDaemon(
            relay.QemuSerialProcess(command),
            provider,
            paths=paths,
            token=token,
            session_id=session_id,
            provider_name=args.provider,
            model_name=args.model or (
                relay.DEEPSEEK_DEFAULT_MODEL if args.provider == "deepseek" else ""
            ),
            max_payload=args.max_payload_bytes,
            max_rounds=args.max_rounds,
            max_tokens=args.max_output_tokens,
            boot_timeout=args.boot_timeout,
            quiet=args.quiet,
            guest_profile=args.guest_profile,
        )

        def stop(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
            assert daemon is not None
            daemon.request_stop()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        daemon.run()
        return 0
    except (relay.RelayError, local.LocalProtocolError, ValueError, OSError) as error:
        code = error.code if isinstance(error, relay.RelayError) else "CONFIGURATION"
        message = error.public_message if isinstance(error, relay.RelayError) else str(error)
        print(f"agentos_relayd: {code}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
