#!/usr/bin/env python3
"""Long-running AgentOS QEMU/model relay daemon.

The daemon owns transport, TLS, terminal fan-out and bounded Host workspace
reads. Conversation history and tool selection remain in the Guest Agent loop.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

# `python -I -S path/to/script.py` intentionally omits the script directory.
# Bootstrap only this audited sibling directory; no site packages are needed.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_local_protocol as local  # noqa: E402
import agentos_nexus_contract as nexus_contract  # noqa: E402
import agentos_nexus_task_ledger as nexus_task_ledger  # noqa: E402
import agentos_workspace as workspace  # noqa: E402
import guest_llm_relay as relay  # noqa: E402


MAX_USER_MESSAGE_BYTES = relay.MAX_GOAL_BYTES
NEXUS_MAX_USER_MESSAGE_BYTES = relay.NEXUS_MAX_GOAL_BYTES
NEXUS_MAX_FINAL_BYTES = relay.NEXUS_MAX_FINAL_BYTES
NEXUS_MAX_PAYLOAD_BYTES = relay.PROTOCOL_MAX_PAYLOAD_CEILING
NEXUS_MAX_MODEL_REQUEST_BYTES = 15360
NEXUS_FULL_U64_MAX = (1 << 64) - 1
MAX_LOCAL_CLIENTS = 8
MAX_CLIENT_QUEUE = 128
MAX_PEER_INBOUND_QUEUE = 32
MAX_LOCAL_DRAIN = 16
# Admission queues the replay prefix before the peer writer starts.  Reserve
# half of that bounded queue for live events arriving just after publication.
MAX_KERNEL_IDENTITY_REPLAY_PIDS = MAX_CLIENT_QUEUE // 2
MAX_MODEL_ERROR_MESSAGE_BYTES = 240
SERIAL_WRITE_TIMEOUT_SECONDS = 5.0
APPROVAL_TIMEOUT_SECONDS = 25.0
INTERACTIVE_PROVIDER_TIMEOUT_SECONDS = 100.0
NEXUS_INTERACTIVE_PROVIDER_TIMEOUT_SECONDS = 600.0
SHUTDOWN_GRACE_SECONDS = 5.0
NEXUS_TURN_PROOF_GRACE_SECONDS = 2.0
DEFAULT_BOOT_TIMEOUT_SECONDS = 120.0
READY_LINE = relay.GUEST_RELAY_READY_LINE
NEXUS_READY_LINE = b"agentnexus_ucore: relay_ready=1 nexus=1"
NEXUS_MAX_ROUNDS = 16
NEXUS_MAX_RETRIES = 32
NEXUS_MAX_PROVIDER_INFLIGHT = 2
NEXUS_HISTORY_TURNS = 4
NEXUS_MAX_HISTORY_PROJECTIONS = NEXUS_HISTORY_TURNS
NEXUS_AUTONOMY_CONTRACT = (
    nexus_contract.CONTRACT_VERSION,
    nexus_contract.SYSTEM_POLICY_SHA256,
    nexus_contract.TOOL_CATALOG_SHA256,
)
NEXUS_SYSTEM_POLICY = nexus_contract.SYSTEM_PROMPT
NEXUS_TOOL_CATALOG = [dict(tool) for tool in nexus_contract.TOOLS]
NEXUS_TOOL_CATALOG_JSON = relay.canonical_json_bytes(NEXUS_TOOL_CATALOG).decode("utf-8")
NEXUS_TOOL_NAMES = frozenset(str(tool["name"]) for tool in NEXUS_TOOL_CATALOG)
NEXUS_CONTROL_CONTEXT_PREFIX = nexus_contract.CONTROL_CONTEXT_PREFIX
NEXUS_FINAL_ONLY_SYSTEM_SUFFIX = (
    "Trusted relay final-response instruction: answer the user's request now with "
    "a natural, direct conclusion in the user's language. Do not call, request, "
    "or describe any tool. Do not output DSML, tool_calls, invoke, parameter, or "
    "any tool markup. Keep the answer under 1800 UTF-8 bytes."
)
NEXUS_DSML_TOOL_CALLS_OPEN = "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
NEXUS_DSML_TOOL_CALLS_CLOSE = "</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
NEXUS_DSML_INVOKE_OPEN = "<\uff5c\uff5cDSML\uff5c\uff5cinvoke "
NEXUS_DSML_INVOKE_CLOSE = "</\uff5c\uff5cDSML\uff5c\uff5cinvoke>"
NEXUS_PROVENANCE_ALL = (1 << 6) - 1
NEXUS_SUCCESS_PROVENANCE = {
    "search_files": 60,
    "read_file": 60,
    "inspect_system": 53,
}
NEXUS_WORKSPACE_TOOLS = frozenset(("search_files", "read_file"))
NEXUS_WORKSPACE_OBSERVATION_RESULT = (
    "workspace_observation_ready;agentos_catalog=1;task_channel=1"
)
NEXUS_SYSTEM_OBSERVATION_RESULT = "system_observation_ready;task_channel=1"
NEXUS_WORKSPACE_RESULT_MAX_BYTES = 2800
NEXUS_WORKSPACE_MANIFEST_MAX_BYTES = 12_000
NEXUS_FILE_READ_MAX_LINES = 64
NEXUS_WORKSPACE_RESTART_MAX = 2
NEXUS_FEATURES = ("task_event_v1", "workspace_roundtrip_v1")
NEXUS_FINAL_PROOF_FIELDS = (
    "version",
    "turn_id",
    "request_id",
    "final_corr_id",
    "final_request_sha256",
    "final_response_sha256",
    "provider_proof_sha256",
    "final_task_root",
    "final_artifact_root",
)
NEXUS_TOOL_EVENT_FIELDS = frozenset(
    (
        "turn_id",
        "request_id",
        "corr_id",
        "tool",
        "status",
        "sequence",
        "value0",
        "value1",
        "value2",
        "result",
        "model_projection",
        "context_seq",
        "provenance",
        "projection_sha256",
        "result_sha256",
        "data_trust",
        "artifact_sha256",
        "workspace_source_sha256",
    )
)
NEXUS_WORKSPACE_REQUEST_FIELDS = frozenset(
    (
        "version",
        "turn_id",
        "request_id",
        "corr_id",
        "task_id",
        "tool",
        "operation",
        "attempt",
        "workspace_generation",
        "arguments_sha256",
        "arguments",
    )
)
NEXUS_WORKSPACE_OPERATIONS = frozenset(("manifest", "search", "read"))
NEXUS_WORKSPACE_STATUSES = frozenset(("ok", "stale", "error"))
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
        "source_pid",
        "target_pid",
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
TASK_ROLES = frozenset(("coordinator", "system", "research", "relay"))
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
        "rounds",
        "attempt",
        "attempts",
        "operation",
        "workspace_generation",
        "arguments_sha256",
        "objects_sha256",
        "manifest_cursor",
        "manifest_next_cursor",
        "manifest_eof",
        "workspace_source_sha256",
        "content_bytes",
        "content_sha256",
        "retries",
        "max_retries",
        "request_sha256",
        "raw_guest_request_sha256",
        "history_bindings",
        "context_path",
        "projection_sha256",
        "result_sha256",
        "request_contains_user",
        "user_message_index",
        "response_sha256",
        "user_content_sha256",
        "user_bytes",
        "provider",
        "model",
        "transport",
        "adapter_success",
        "endpoint_origin",
        "provider_endpoint",
        "http_status",
        "provider_request_sha256",
        "provider_response_sha256",
        "selected_reply_sha256",
        "provider_response_id",
        "attempt_count",
        "tool_choice_mode",
        "raw_tool_call_count",
        "selected_index",
        "adaptation",
        "forced_tool",
        "selected_tool_sha256",
        "final_request_sha256",
        "final_corr_id",
        "final_response_sha256",
        "provider_proof_sha256",
        "final_task_root",
        "final_artifact_root",
        "final_proof_root",
        "reason",
        "source",
    )
)
FULL_U64_CONTROL_FIELDS = frozenset(
    ("control_id", "agent_control_id", "actor_control_id")
)


def _positive_u64(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= relay.MAX_SEQUENCE
    ):
        raise relay.WireProtocolError("BAD_REQUEST", f"{label} must be positive u64")
    return value


def _context_path_metadata(value: object) -> dict[str, object] | None:
    """Copy only the body-free metadata authorized by the Nexus V4 contract."""

    root_fields = {
        "version",
        "branch_generation",
        "visible_head_sequence",
        "current_user_sequence",
        "turns",
    }
    turn_fields = {
        "turn_id",
        "request_id",
        "user_sequence",
        "final_sequence",
        "sha256",
    }
    if not isinstance(value, Mapping) or set(value) != root_fields:
        return None
    version = value.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != nexus_contract.CONTEXT_PATH_VERSION
    ):
        return None
    for field_name in (
        "branch_generation",
        "visible_head_sequence",
        "current_user_sequence",
    ):
        item = value.get(field_name)
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 1 <= item <= NEXUS_FULL_U64_MAX
        ):
            return None
    turns = value.get("turns")
    if (
        not isinstance(turns, list)
        or len(turns) > nexus_contract.CONTEXT_PATH_MAX_TURNS
    ):
        return None
    copied_turns: list[dict[str, object]] = []
    for turn in turns:
        if not isinstance(turn, Mapping) or set(turn) != turn_fields:
            return None
        for field_name in (
            "turn_id",
            "request_id",
            "user_sequence",
            "final_sequence",
        ):
            item = turn.get(field_name)
            if (
                not isinstance(item, int)
                or isinstance(item, bool)
                or not 1 <= item <= NEXUS_FULL_U64_MAX
            ):
                return None
        digest = turn.get("sha256")
        if (
            not isinstance(digest, str)
            or relay.WIRE_DIGEST_RE.fullmatch(digest) is None
        ):
            return None
        copied_turns.append(
            {
                "turn_id": turn["turn_id"],
                "request_id": turn["request_id"],
                "user_sequence": turn["user_sequence"],
                "final_sequence": turn["final_sequence"],
                "sha256": turn["sha256"],
            }
        )
    return {
        "version": value["version"],
        "branch_generation": value["branch_generation"],
        "visible_head_sequence": value["visible_head_sequence"],
        "current_user_sequence": value["current_user_sequence"],
        "turns": copied_turns,
    }


def _nexus_ledger_wire_error(
    error: nexus_task_ledger.NexusTaskLedgerError,
) -> relay.WireProtocolError:
    return relay.WireProtocolError(error.code, error.reason)


def _u64(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= relay.MAX_SEQUENCE
    ):
        raise relay.WireProtocolError("BAD_TASK_EVENT", f"{label} must be u64")
    return value


def _positive_full_u64(value: object, label: str, *, code: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= NEXUS_FULL_U64_MAX
    ):
        raise relay.WireProtocolError(code, f"{label} must be positive full u64")
    return value


def _full_u64(value: object, label: str, *, code: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= NEXUS_FULL_U64_MAX
    ):
        raise relay.WireProtocolError(code, f"{label} must be full u64")
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


def _bounded_int(
    value: object,
    label: str,
    *,
    code: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise relay.WireProtocolError(code, f"{label} is outside its valid range")
    return value


def _safe_text(
    value: object,
    label: str,
    *,
    code: str,
    maximum: int,
    empty: bool = False,
) -> str:
    try:
        result = _text(value, label, maximum=maximum, empty=empty)
    except relay.WireProtocolError as error:
        raise relay.WireProtocolError(code, error.public_message) from error
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in result):
        raise relay.WireProtocolError(code, f"{label} contains control characters")
    return result


def _digest(value: object, label: str, *, code: str) -> str:
    result = _safe_text(value, label, code=code, maximum=64)
    if relay.WIRE_DIGEST_RE.fullmatch(result) is None:
        raise relay.WireProtocolError(code, f"{label} is malformed")
    return result


def _strict_json_object(value: str, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=relay._reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (ValueError, json.JSONDecodeError, RecursionError):
        raise relay.WireProtocolError(
            "BAD_REQUEST", f"{label} is not a strict JSON object"
        ) from None
    if not isinstance(parsed, dict):
        raise relay.WireProtocolError(
            "BAD_REQUEST", f"{label} is not a strict JSON object"
        )
    return parsed


def _utf8_prefix(value: str, maximum: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= maximum:
        return value
    return raw[:maximum].decode("utf-8", errors="ignore")


def _model_error_message(value: str) -> str:
    # The Guest JSON decoder intentionally rejects U+0000.  Provider diagnostics
    # are untrusted data, so make that byte visible and harmless before applying
    # the negotiated UTF-8 byte limit.
    return _utf8_prefix(value.replace("\0", "[NUL]"), MAX_MODEL_ERROR_MESSAGE_BYTES)


def _model_error_code(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value):
        return value
    return "PROVIDER_FAILURE"


def _json_string_payload_bytes(value: str) -> int:
    raw = value.encode("utf-8")
    return sum(
        6 if byte < 0x20 else (2 if byte in (ord('"'), ord("\\")) else 1)
        for byte in raw
    )


def _nexus_tool_text(
    value: object,
    label: str,
    *,
    maximum_codepoints: int,
    empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value)
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise relay.ProviderError(
            "BAD_TOOL_ARGUMENTS",
            f"{label} is not valid bounded text",
            retryable=True,
        )
    try:
        raw = value.encode("utf-8")
        escaped_bytes = _json_string_payload_bytes(value)
    except (UnicodeEncodeError, TypeError, ValueError):
        raise relay.ProviderError(
            "BAD_TOOL_ARGUMENTS",
            f"{label} is not valid bounded text",
            retryable=True,
        ) from None
    if len(value) > maximum_codepoints:
        raise relay.ProviderError(
            "BAD_TOOL_ARGUMENTS",
            f"{label} exceeds its Nexus schema maxLength",
            retryable=True,
        )
    if (
        len(raw) > relay.MAX_NEXUS_TOOL_ARGUMENT_STRING_BYTES
        or escaped_bytes > relay.MAX_NEXUS_TOOL_ARGUMENT_STRING_BYTES
    ):
        raise relay.ProviderError(
            "TOOL_ARGUMENT_BUDGET",
            f"{label} exceeds the Nexus Guest request budget",
            retryable=True,
        )
    return value


def _nexus_tool_u32(
    value: object, label: str, *, minimum: int = 0, maximum: int = 0xFFFFFFFF
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise relay.ProviderError(
            "BAD_TOOL_ARGUMENTS",
            f"{label} is outside the Nexus Guest integer range",
            retryable=True,
        )
    return value


def _validate_nexus_tool_arguments(
    tool: str, arguments: object
) -> dict[str, object]:
    if not isinstance(arguments, dict):
        raise relay.ProviderError(
            "BAD_TOOL_ARGUMENTS",
            "Nexus tool arguments must be an object",
            retryable=True,
        )
    keys = set(arguments)
    if tool == "search_files":
        if not {"query"}.issubset(keys) or not keys.issubset(
            {"query", "path_prefix"}
        ):
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "search_files arguments do not match the Nexus contract",
                retryable=True,
            )
        query = _nexus_tool_text(
            arguments["query"],
            "search_files.query",
            maximum_codepoints=95,
            empty=True,
        )
        try:
            workspace._validate_text(
                query, "query", workspace.MAX_QUERY_BYTES, empty=True
            )
        except workspace._WorkspaceInputError as error:
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "search_files.query is not workspace-safe text",
                retryable=True,
            ) from error
        if "path_prefix" in arguments:
            path_prefix = _nexus_tool_text(
                arguments["path_prefix"],
                "search_files.path_prefix",
                maximum_codepoints=111,
                empty=True,
            )
            try:
                workspace._validate_relative_path(
                    path_prefix,
                    "path_prefix",
                    workspace.MAX_PREFIX_BYTES,
                    empty=True,
                    trailing_slash=True,
                )
            except workspace._WorkspaceInputError as error:
                raise relay.ProviderError(
                    "BAD_TOOL_ARGUMENTS",
                    "search_files.path_prefix is not a safe relative path",
                    retryable=True,
                ) from error
    elif tool == "read_file":
        if keys != {"path", "start_line", "max_lines"}:
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "read_file arguments do not match the Nexus contract",
                retryable=True,
            )
        path = _nexus_tool_text(
            arguments["path"], "read_file.path", maximum_codepoints=255
        )
        try:
            workspace._validate_relative_path(
                path,
                "path",
                workspace.MAX_PATH_BYTES,
                empty=False,
                trailing_slash=False,
            )
        except workspace._WorkspaceInputError as error:
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "read_file.path is not a safe relative path",
                retryable=True,
            ) from error
        _nexus_tool_u32(arguments["start_line"], "read_file.start_line", minimum=1)
        _nexus_tool_u32(
            arguments["max_lines"], "read_file.max_lines", minimum=1,
            maximum=NEXUS_FILE_READ_MAX_LINES,
        )
    elif tool == "inspect_system":
        if keys != {"operation"}:
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "inspect_system arguments do not match the Nexus contract",
                retryable=True,
            )
        operation = _nexus_tool_text(
            arguments["operation"],
            "inspect_system.operation",
            maximum_codepoints=9,
        )
        if operation not in ("status", "processes", "context"):
            raise relay.ProviderError(
                "BAD_TOOL_ARGUMENTS",
                "inspect_system.operation is unsupported",
                retryable=True,
            )
    else:
        raise relay.ProviderError(
            "UNADVERTISED_TOOL",
            "provider selected a tool outside the Nexus contract",
            retryable=True,
        )
    return arguments


def _provider_name(
    provider: relay.ModelProvider, configured: str | None
) -> str:
    allowed = frozenset(("openai", "anthropic", "deepseek", "replay"))
    if isinstance(provider, relay.ReplayProvider):
        inferred = "replay"
    elif isinstance(provider, relay.DeepSeekProvider):
        inferred = "deepseek"
    elif isinstance(provider, relay.AnthropicMessagesProvider):
        inferred = "anthropic"
    elif isinstance(provider, relay.OpenAICompatibleProvider):
        inferred = "openai"
    else:
        inferred = "adapter"
    if configured is None:
        return inferred
    if configured not in allowed or (
        inferred != "adapter" and configured != inferred
    ):
        raise ValueError("provider name does not match its adapter")
    return configured


def _provider_model_name(
    provider: relay.ModelProvider, configured: str | None
) -> str:
    value = getattr(provider, "model", "") if configured is None else configured
    if not isinstance(value, str):
        raise ValueError("provider model is malformed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("provider model is malformed") from error
    if len(encoded) > 128 or any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value
    ):
        raise ValueError("provider model is malformed")
    return value


def _provider_endpoint_origin(provider: relay.ModelProvider) -> str:
    client = getattr(provider, "client", None)
    if not isinstance(client, relay.JsonHttpsClient):
        return ""
    try:
        parsed = urllib.parse.urlsplit(client.endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() != "https" or not hostname:
        return ""
    host = hostname.rstrip(".").lower()
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority = f"{authority}:{port}"
    return f"https://{authority}"


def _provider_receipt_fields(receipt: relay.ProviderReceipt) -> dict[str, object]:
    value: dict[str, object] = {
        "adapter_success": receipt.adapter_success,
        "transport": receipt.transport,
        "provider_endpoint": receipt.endpoint,
        "http_status": receipt.http_status,
        "provider_request_sha256": receipt.request_sha256,
        "provider_response_sha256": receipt.response_sha256,
        "selected_reply_sha256": receipt.selected_reply_sha256,
        "attempt_count": receipt.attempt_count,
        "tool_choice_mode": receipt.tool_choice_mode,
        "raw_tool_call_count": receipt.raw_tool_call_count,
        "selected_index": receipt.selected_index,
        "adaptation": receipt.adaptation,
        "forced_tool": receipt.forced_tool,
        "selected_tool_sha256": receipt.selected_tool_sha256,
    }
    if receipt.provider_response_id:
        value["provider_response_id"] = receipt.provider_response_id
    return value


def _history_records(request: Mapping[str, object]) -> dict[int, dict[str, object]]:
    messages = request.get("messages", ())
    if not isinstance(messages, list):
        return {}
    calls: dict[int, dict[str, object]] = {}
    records: dict[int, dict[str, object]] = {}
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            tool_use = message.get("tool_use")
            if isinstance(tool_use, Mapping):
                corr_id = tool_use.get("corr_id")
                tool = tool_use.get("tool")
                if (
                    isinstance(corr_id, int)
                    and not isinstance(corr_id, bool)
                    and corr_id > 0
                    and isinstance(tool, str)
                    and relay.TOOL_NAME_RE.fullmatch(tool) is not None
                    and isinstance(tool_use.get("arguments"), dict)
                ):
                    if corr_id in calls:
                        raise relay.WireProtocolError(
                            "BAD_REQUEST", "tool history repeats an assistant call"
                        )
                    calls[corr_id] = {
                        "index": index,
                        "tool": tool,
                        "arguments_canonical": relay.canonical_json_bytes(
                            tool_use["arguments"]
                        ).decode("utf-8"),
                    }
            continue
        if message.get("role") != "tool":
            continue
        corr_id = message.get("tool_corr_id")
        content = message.get("content")
        if (
            not isinstance(corr_id, int)
            or isinstance(corr_id, bool)
            or corr_id <= 0
            or not isinstance(content, str)
        ):
            continue
        call = calls.get(corr_id)
        if call is None or int(call["index"]) + 1 != index or corr_id in records:
            raise relay.WireProtocolError(
                "BAD_REQUEST", "tool history has no adjacent unique assistant binding"
            )
        wrapper = _strict_json_object(content, "tool history wrapper")
        model_projection = wrapper.get("model_projection")
        runtime_observation = wrapper.get("runtime_observation")
        if model_projection is not None and runtime_observation is not None:
            raise relay.WireProtocolError(
                "BAD_REQUEST", "tool history repeats its data projection"
            )
        projection = (
            runtime_observation
            if runtime_observation is not None
            else model_projection
        )
        if projection is not None and not isinstance(projection, str):
            raise relay.WireProtocolError(
                "BAD_REQUEST", "tool data projection must be text"
            )
        is_error = message.get("is_error", False)
        if not isinstance(is_error, bool):
            raise relay.WireProtocolError(
                "BAD_REQUEST", "tool history error marker is malformed"
            )
        records[corr_id] = {
            "index": index,
            "tool": call["tool"],
            "arguments_canonical": call["arguments_canonical"],
            "wrapper": wrapper,
            "wrapper_canonical": relay.canonical_json_bytes(wrapper).decode("utf-8"),
            "is_error": is_error,
            "projection_sha256": (
                hashlib.sha256(projection.encode("utf-8")).hexdigest()
                if projection is not None
                else ""
            ),
            "projection_field": (
                "runtime_observation"
                if runtime_observation is not None
                else "model_projection" if model_projection is not None else ""
            ),
            "data_trust": wrapper.get("data_trust", ""),
        }
    return records


def _history_bindings(
    records: Mapping[int, Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    values = [
        {
            "tool_corr_id": corr_id,
            "tool": str(record["tool"]),
            "projection_sha256": str(record["projection_sha256"]),
            "projection_field": str(record["projection_field"]),
            "data_trust": str(record["data_trust"]),
        }
        for corr_id, record in records.items()
        if record.get("projection_sha256")
    ]
    return tuple(values[-NEXUS_MAX_HISTORY_PROJECTIONS:])


def _inspect_system_projection_values(
    operation: object, projection: object
) -> tuple[int, int]:
    specifications = {
        "status": (
            "get_system_status",
            "process_count",
            "agent_count",
            "uptime_tick",
        ),
        "processes": (
            "query_process",
            "process_count",
            "agent_count",
            "runnable_count",
        ),
        "context": (
            "ctx_stat",
            "context_base",
            "context_size",
            "call_count",
        ),
    }
    specification = specifications.get(operation)
    if specification is None or not isinstance(projection, str):
        raise relay.WireProtocolError(
            "BAD_TOOL_EVENT", "system observation projection is malformed"
        )
    try:
        raw = projection.encode("utf-8")
    except UnicodeEncodeError as error:
        raise relay.WireProtocolError(
            "BAD_TOOL_EVENT", "system observation projection is not UTF-8"
        ) from error
    if not projection.endswith("\n") or len(raw) > NEXUS_WORKSPACE_RESULT_MAX_BYTES:
        raise relay.WireProtocolError(
            "BAD_TOOL_EVENT", "system observation projection is outside bounds"
        )
    tool, first_label, second_label, omitted = specification
    prefix = (
        "scope=this_boot_guest_runtime\n"
        "content_untrusted=1\n"
        f"operation={operation}\n"
        f"tool={tool}\n"
        "status=0\n"
    )
    suffix = f"volatile_fields_omitted={omitted}\n"
    if not projection.startswith(prefix) or not projection.endswith(suffix):
        raise relay.WireProtocolError(
            "BAD_TOOL_EVENT", "system observation projection shape is malformed"
        )
    value_lines = projection[len(prefix) : -len(suffix)].split("\n")
    if len(value_lines) != 3 or value_lines[-1] != "":
        raise relay.WireProtocolError(
            "BAD_TOOL_EVENT", "system observation values are malformed"
        )
    values: list[int] = []
    for line, label in zip(value_lines[:2], (first_label, second_label)):
        marker = f"{label}="
        text = line[len(marker) :] if line.startswith(marker) else ""
        if not text or (len(text) > 1 and text.startswith("0")) or not text.isascii() or not text.isdecimal():
            raise relay.WireProtocolError(
                "BAD_TOOL_EVENT", "system observation value is malformed"
            )
        value = int(text, 10)
        if value > (1 << 64) - 1:
            raise relay.WireProtocolError(
                "BAD_TOOL_EVENT", "system observation value is outside bounds"
            )
        values.append(value)
    return values[0], values[1]


def _bounded_workspace_result(
    value: object, *, maximum: int = NEXUS_WORKSPACE_RESULT_MAX_BYTES
) -> str:
    if not isinstance(value, str):
        return "workspace_error=invalid_host_result"
    value = value.replace("\0", "[NUL]")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return "workspace_error=invalid_host_result"
    if len(encoded) > maximum:
        return "workspace_error=host_result_too_large"
    return value


def _workspace_objects_sha256(arguments: Mapping[str, object]) -> str:
    candidates: object
    if "candidates" in arguments:
        candidates = arguments["candidates"]
    elif all(key in arguments for key in ("object_id", "path", "revision")):
        candidates = [
            {
                "object_id": arguments["object_id"],
                "path": arguments["path"],
                "revision": arguments["revision"],
            }
        ]
    else:
        candidates = []
    return hashlib.sha256(relay.canonical_json_bytes(candidates)).hexdigest()


def _workspace_content_fields(content: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for line in content.split("\n"):
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError("workspace content field is malformed")
        fields.append((key, value))
    return fields


def _parse_workspace_manifest_content(
    content: str,
) -> tuple[int, int, bool, list[dict[str, str]]]:
    lines = content.split("\n")
    if not lines or lines[0] != "workspace_manifest_v1":
        raise ValueError("workspace manifest marker is malformed")
    fields = _workspace_content_fields("\n".join(lines[1:]))
    if len(fields) < 4 or [item[0] for item in fields[:4]] != [
        "cursor",
        "next_cursor",
        "entry_count",
        "eof",
    ]:
        raise ValueError("workspace manifest header is malformed")
    cursor = int(fields[0][1], 10)
    next_cursor = int(fields[1][1], 10)
    count = int(fields[2][1], 10)
    eof_value = int(fields[3][1], 10)
    if (
        cursor < 0
        or next_cursor != cursor + count
        or count < 0
        or eof_value not in (0, 1)
        or len(fields) != 4 + 5 * count
    ):
        raise ValueError("workspace manifest bounds are malformed")
    entries: list[dict[str, str]] = []
    for index in range(1, count + 1):
        offset = 4 + (index - 1) * 5
        expected = [
            f"entry[{index}].object_id",
            f"entry[{index}].path",
            f"entry[{index}].revision",
            f"entry[{index}].size",
            f"entry[{index}].kind",
        ]
        group = fields[offset : offset + 5]
        if [item[0] for item in group] != expected:
            raise ValueError("workspace manifest entry is malformed")
        object_id, path, revision, size_text, kind = [item[1] for item in group]
        if (
            relay.WIRE_DIGEST_RE.fullmatch(object_id) is None
            or relay.WIRE_DIGEST_RE.fullmatch(revision) is None
            or kind != "file"
            or int(size_text, 10) < 0
        ):
            raise ValueError("workspace manifest identity is malformed")
        workspace._validate_relative_path(
            path,
            "path",
            workspace.MAX_PATH_BYTES,
            empty=False,
            trailing_slash=False,
        )
        entries.append(
            {"object_id": object_id, "path": path, "revision": revision}
        )
    return cursor, next_cursor, bool(eof_value), entries


def _parse_workspace_search_content(
    content: str,
) -> tuple[str, int, bool, list[dict[str, object]]]:
    lines = content.split("\n")
    if not lines or lines[0] != "workspace_search_v1":
        raise ValueError("workspace search marker is malformed")
    fields = _workspace_content_fields("\n".join(lines[1:]))
    if len(fields) < 5 or [item[0] for item in fields[:5]] != [
        "content_untrusted",
        "query",
        "candidate_count",
        "match_count",
        "truncated",
    ]:
        raise ValueError("workspace search header is malformed")
    if fields[0][1] != "1":
        raise ValueError("workspace search trust marker is malformed")
    query = fields[1][1]
    candidate_count = int(fields[2][1], 10)
    match_count = int(fields[3][1], 10)
    truncated_value = int(fields[4][1], 10)
    if (
        not 0 <= candidate_count <= workspace.MAX_CANDIDATES
        or not 0 <= match_count <= workspace.MAX_RESULTS
        or truncated_value not in (0, 1)
        or len(fields) != 5 + 6 * match_count
    ):
        raise ValueError("workspace search bounds are malformed")
    matches: list[dict[str, object]] = []
    for index in range(1, match_count + 1):
        offset = 5 + (index - 1) * 6
        expected = [
            f"match[{index}].object_id",
            f"match[{index}].path",
            f"match[{index}].revision",
            f"match[{index}].kind",
            f"match[{index}].line",
            f"match[{index}].snippet",
        ]
        group = fields[offset : offset + 6]
        if [item[0] for item in group] != expected:
            raise ValueError("workspace search match is malformed")
        object_id, path, revision, kind, line_text, snippet = [
            item[1] for item in group
        ]
        if (
            relay.WIRE_DIGEST_RE.fullmatch(object_id) is None
            or relay.WIRE_DIGEST_RE.fullmatch(revision) is None
            or kind not in ("content", "path", "file")
        ):
            raise ValueError("workspace search match identity is malformed")
        line = int(line_text, 10)
        if line < 0 or (kind == "content" and line == 0):
            raise ValueError("workspace search line is malformed")
        workspace._validate_relative_path(
            path,
            "path",
            workspace.MAX_PATH_BYTES,
            empty=False,
            trailing_slash=False,
        )
        matches.append(
            {
                "object_id": object_id,
                "kind": kind,
                "path": path,
                "revision": revision,
                "line": line,
                "snippet": snippet,
            }
        )
    return query, candidate_count, bool(truncated_value), matches


def _render_workspace_search_aggregate(
    query: str,
    path_prefix: str,
    matches: Sequence[Mapping[str, object]],
    truncated: bool,
) -> str:
    retained = list(matches[: workspace.MAX_RESULTS])
    clipped = bool(truncated or len(matches) > len(retained))
    while True:
        lines = [
            "workspace_search",
            "content_untrusted=1",
            f"query={query}",
            f"path_prefix={path_prefix}",
            f"match_count={len(retained)}",
            f"truncated={int(clipped)}",
        ]
        for index, match in enumerate(retained, 1):
            lines.extend(
                (
                    f"match[{index}].kind={match['kind']}",
                    f"match[{index}].path={match['path']}",
                    f"match[{index}].line={match['line']}",
                    f"match[{index}].snippet={match['snippet']}",
                )
            )
        value = "\n".join(lines)
        if len(value.encode("utf-8")) <= NEXUS_WORKSPACE_RESULT_MAX_BYTES:
            return value
        if not retained:
            raise ValueError("workspace search header is too large")
        retained.pop()
        clipped = True


def _workspace_source_record(
    *,
    operation: str,
    attempt: int,
    request_generation: str,
    result_generation: str,
    arguments_sha256: str,
    objects_sha256: str,
    content_bytes: int,
    content_sha256: str,
) -> str:
    return (
        "workspace_source_attempt_v1\n"
        f"operation={operation}\n"
        f"attempt={attempt}\n"
        f"request_generation={request_generation or ('0' * 64)}\n"
        f"result_generation={result_generation}\n"
        f"arguments_sha256={arguments_sha256}\n"
        f"objects_sha256={objects_sha256}\n"
        "status=ok\n"
        f"content_bytes={content_bytes}\n"
        f"content_sha256={content_sha256}\n"
    )


@dataclass
class ActiveTurn:
    turn_id: int
    request_id: int
    generation: int
    user_content_sha256: str
    user_bytes: int
    user_content: str = field(repr=False)
    context_branch_generation: int = 0
    context_start_sequence: int = 0
    context_start_known: bool = False
    context_user_sequence: int = 0
    context_visible_head_sequence: int = 0
    rounds: int = 0
    retries: int = 0
    attempts: int = 0
    cancelled: bool = False


@dataclass(frozen=True)
class ProviderCompletion:
    generation: int
    turn_id: int
    request_id: int
    corr_id: int
    request_sha256: str
    raw_guest_request_sha256: str
    history_bindings: tuple[dict[str, object], ...]
    user_message_index: int
    model: str
    # Host-private delivery policy; never part of the Guest request/proof.
    final_only: bool
    reply: relay.ModelReply | None
    error: relay.RelayError | None


_NEXUS_RETRYABLE_RESPONSE_ERROR_CODES = frozenset(
    (
        "BAD_PROVIDER_RESPONSE",
        "BAD_HTTP_RESPONSE",
        "BAD_TOOL_ARGUMENTS",
        "HTTP_RESPONSE_TOO_LARGE",
        "INCOMPLETE_MODEL_RESPONSE",
        "MIXED_MODEL_RESPONSE",
        "MULTIPLE_TOOL_CALLS",
        "TOOL_ARGUMENT_SCHEMA_MISMATCH",
        "TOOL_CHOICE_MISMATCH",
        "TOOL_NOT_ADVERTISED",
    )
)


def _nexus_model_error(error: relay.RelayError) -> relay.RelayError:
    """Make provider-output shape failures replanable, not transport fatals."""

    if (
        isinstance(error, relay.ProviderError)
        and error.code in _NEXUS_RETRYABLE_RESPONSE_ERROR_CODES
        and not error.retryable
    ):
        return relay.ProviderError(
            error.code, error.public_message, retryable=True
        )
    return error


def _is_dsml_tool_calls_markup(content: str) -> bool:
    """Recognize a final-only DeepSeek tool call rendered as literal text."""

    value = content.strip()
    return (
        value.startswith(NEXUS_DSML_TOOL_CALLS_OPEN)
        and value.endswith(NEXUS_DSML_TOOL_CALLS_CLOSE)
        and NEXUS_DSML_INVOKE_OPEN in value
        and NEXUS_DSML_INVOKE_CLOSE in value
    )


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
        max_payload: int | None = None,
        max_rounds: int | None = None,
        max_tokens: int | None = None,
        guest_profile: str = "agentlive",
        provider_name: str | None = None,
        model_name: str | None = None,
        workspace_reader: workspace.WorkspaceReader | None = None,
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
        profile_max_tokens = (
            relay.NEXUS_MAX_OUTPUT_TOKENS
            if guest_profile == "nexus"
            else relay.MAX_OUTPUT_TOKENS
        )
        if max_tokens is None:
            max_tokens = (
                relay.NEXUS_MAX_OUTPUT_TOKENS
                if guest_profile == "nexus"
                else relay.DEFAULT_MAX_OUTPUT_TOKENS
            )
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not 1 <= max_tokens <= profile_max_tokens
        ):
            raise ValueError("max_tokens is outside Host policy")
        if max_payload is None:
            max_payload = (
                NEXUS_MAX_PAYLOAD_BYTES
                if guest_profile == "nexus"
                else relay.PROTOCOL_MAX_PAYLOAD_BYTES
            )
        if guest_profile == "nexus":
            if max_payload != NEXUS_MAX_PAYLOAD_BYTES:
                raise ValueError(
                    "max_payload must exactly match the Nexus Guest profile"
                )
        elif not 3072 <= max_payload <= relay.PROTOCOL_MAX_PAYLOAD_BYTES:
            raise ValueError(
                "AgentLive max_payload must remain in the compatible 3072..4096 range"
            )
        self.provider = provider
        self.provider_name = _provider_name(provider, provider_name)
        self.model_name = _provider_model_name(provider, model_name)
        self.endpoint_origin = _provider_endpoint_origin(provider)
        self.provider_transport = (
            "replay"
            if self.provider_name == "replay"
            else ("https" if self.endpoint_origin else "adapter")
        )
        defer_call_commits = getattr(provider, "defer_call_commits", None)
        if callable(defer_call_commits):
            defer_call_commits()
        self.send_line = send_line
        self.controller_sink = controller_sink
        self.telemetry_sink = telemetry_sink
        self.session_id = session_id or secrets.token_hex(16)
        self.guest_profile = guest_profile
        if guest_profile != "nexus" and workspace_reader is not None:
            raise ValueError("workspace access is only valid for the Nexus profile")
        self.workspace_reader = workspace_reader
        self.approval_timeout_seconds = APPROVAL_TIMEOUT_SECONDS
        self.max_tool_arguments = (
            relay.MAX_NEXUS_TOOL_ARGUMENTS
            if guest_profile == "nexus"
            else relay.MAX_TOOL_ARGUMENTS
        )
        self.max_tool_argument_string_bytes = (
            relay.MAX_NEXUS_TOOL_ARGUMENT_STRING_BYTES
            if guest_profile == "nexus"
            else relay.MAX_TOOL_ARGUMENT_STRING_BYTES
        )
        self.max_user_bytes = (
            NEXUS_MAX_USER_MESSAGE_BYTES
            if guest_profile == "nexus"
            else MAX_USER_MESSAGE_BYTES
        )
        self.max_final_bytes = (
            NEXUS_MAX_FINAL_BYTES
            if guest_profile == "nexus"
            else relay.MAX_FINAL_BYTES
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
        self.max_retries = NEXUS_MAX_RETRIES if guest_profile == "nexus" else 0
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
        self._last_final_response: tuple[int, str] | None = None
        self._nexus_tool_ledger: dict[int, dict[str, object]] = {}
        self._nexus_completed_turn_bindings: list[
            tuple[int, int, int, int, str]
        ] = []
        self._nexus_context_head_sequence = 0
        self._nexus_context_head_known = False
        self._last_final_request_sha256 = ""
        self._nexus_final_frozen = False
        self._approval_bindings: set[tuple[int, int, int, str, str]] = set()
        self._model_job: tuple[int, int] | None = None
        self._provider_inflight: set[tuple[int, int]] = set()
        self._provider_results: queue.Queue[ProviderCompletion] = queue.Queue()
        self._controls: dict[int, str] = {}
        self._nexus_lifecycle: tuple[int, int] | None = None
        self._kernel_audit_sequence = 0
        self._kernel_identities: dict[int, tuple[str, int, int, int]] = {}
        self._nexus_task_ledger = (
            nexus_task_ledger.NexusTaskLedger(
                identity_lookup=lambda pid: self._kernel_identities.get(pid),
                require_kernel_identity=True,
            )
            if guest_profile == "nexus"
            else None
        )
        self._nexus_cancel_pending = False
        self._active_last_corr = 0
        self._last_final_response_sha256 = ""
        self._last_final_provider_proof_sha256 = ""
        self._last_final_corr_id = 0
        self._pending_turn_complete: tuple[dict[str, object], str, float] | None = None

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
                {
                    "guest_profile": "nexus",
                    "features": list(NEXUS_FEATURES),
                    "max_retries": self.max_retries,
                    "max_user_bytes": self.max_user_bytes,
                    "max_final_bytes": self.max_final_bytes,
                }
            )
        self._send("HELLO", hello)
        ready: dict[str, object] = {
            "type": "session_ready",
            "session_id": self.session_id,
            "max_rounds": self.max_rounds,
            "guest_profile": self.guest_profile,
        }
        if self.guest_profile == "nexus":
            ready["max_retries"] = self.max_retries
        self._controller(ready)
        telemetry_ready: dict[str, object] = {
            "event": "session_ready",
            "session_id": self.session_id,
            "guest_profile": self.guest_profile,
        }
        if self.guest_profile == "nexus":
            telemetry_ready["max_retries"] = self.max_retries
        self._telemetry(telemetry_ready)

    def submit_user(self, content: object) -> tuple[int, int]:
        self._require_open()
        if self.closing:
            raise relay.WireProtocolError("SESSION_CLOSING", "interactive session is closing")
        if not self.ready:
            raise relay.WireProtocolError("NOT_READY", "Guest is still booting")
        if self.active is not None:
            raise relay.WireProtocolError("TURN_ACTIVE", "a user turn is already active")
        if self._controls:
            raise relay.WireProtocolError(
                "CONTROL_BUSY", "a Guest control request is still pending"
            )
        if (
            self.guest_profile == "nexus"
            and len(self._provider_inflight) >= NEXUS_MAX_PROVIDER_INFLIGHT
        ):
            raise relay.WireProtocolError(
                "PROVIDER_BUSY",
                "too many cancelled provider requests are still completing",
            )
        message = _text(
            content, "user content", maximum=self.max_user_bytes
        )
        message_bytes = message.encode("utf-8")
        if self.guest_profile == "nexus" and (
            "\0" in message
            or _json_string_payload_bytes(message) > self.max_user_bytes
        ):
            raise relay.WireProtocolError(
                "BAD_REQUEST",
                "user content exceeds the Nexus Guest request budget",
            )
        user_content_sha256 = hashlib.sha256(message_bytes).hexdigest()
        turn_id = self._next_turn
        self._next_turn += 1
        request_id = self._allocate_request()
        self._generation += 1
        self.active = ActiveTurn(
            turn_id,
            request_id,
            self._generation,
            user_content_sha256,
            len(message_bytes),
            message,
            context_start_sequence=self._nexus_context_head_sequence,
            context_start_known=self._nexus_context_head_known,
        )
        if self._nexus_task_ledger is not None:
            try:
                self._nexus_task_ledger.begin_turn(turn_id, request_id)
            except nexus_task_ledger.NexusTaskLedgerError as error:
                self.active = None
                raise _nexus_ledger_wire_error(error) from None
        self._last_model_response_corr = 0
        self._last_final_response = None
        self._nexus_tool_ledger.clear()
        self._last_final_request_sha256 = ""
        self._nexus_final_frozen = False
        self._nexus_cancel_pending = False
        self._active_last_corr = 0
        self._last_final_response_sha256 = ""
        self._last_final_provider_proof_sha256 = ""
        self._last_final_corr_id = 0
        self._approval_bindings.clear()
        self._send(
            "USER_MESSAGE",
            {"turn_id": turn_id, "request_id": request_id, "content": message},
        )
        event: dict[str, object] = {
            "type": "turn_started",
            "turn_id": turn_id,
            "request_id": request_id,
        }
        if self.guest_profile == "nexus":
            event.update(
                {
                    "generation": self.active.generation,
                    "user_content_sha256": user_content_sha256,
                    "user_bytes": len(message_bytes),
                }
            )
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
        if self._controls:
            raise relay.WireProtocolError(
                "CONTROL_BUSY", "another Guest control request is still pending"
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
        task_snapshot = (
            self._nexus_task_ledger.snapshot()
            if self._nexus_task_ledger is not None
            else None
        )
        terminal_outcome_owned = bool(
            task_snapshot is not None
            and (
                task_snapshot.termination_cause
                or task_snapshot.provider_final_frozen
            )
        )
        turn.cancelled = True
        self._generation += 1
        self._model_job = None
        self.pending_approval = None
        self._approval_deadline = 0.0
        if not terminal_outcome_owned:
            self._last_model_response_corr = 0
            self._last_final_response = None
            self._last_final_request_sha256 = ""
            self._nexus_final_frozen = False
            self._last_final_response_sha256 = ""
            self._last_final_provider_proof_sha256 = ""
            self._last_final_corr_id = 0
        if self._nexus_task_ledger is not None:
            assert task_snapshot is not None
            cancel_corr = self._active_last_corr
            if not cancel_corr:
                roots = [
                    task for task in task_snapshot.tasks if task.parent_task_id == 0
                ]
                if len(roots) == 1:
                    cancel_corr = roots[0].assigned_corr_id
            if terminal_outcome_owned:
                # A provider final, fatal error, or final-round result already
                # deterministically owns the terminal proof.  Still send CANCEL
                # to wake the Guest, but do not replace or duplicate that cause.
                self._nexus_cancel_pending = False
            elif cancel_corr:
                try:
                    self._nexus_task_ledger.begin_cancel(cancel_corr)
                except nexus_task_ledger.NexusTaskLedgerError as error:
                    raise _nexus_ledger_wire_error(error) from None
            else:
                self._nexus_cancel_pending = True
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
        binding_fields = APPROVAL_BINDING_FIELDS
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
        self._nexus_completed_turn_bindings.clear()
        self._nexus_context_head_sequence = 0
        self._nexus_context_head_known = False
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
        if (
            self.guest_profile == "nexus"
            and frame.kind == "MODEL_REQUEST"
            and len(frame.payload) > NEXUS_MAX_MODEL_REQUEST_BYTES
        ):
            raise relay.WireProtocolError(
                "FRAME_TOO_LARGE",
                "Nexus model request exceeds its negotiated headroom budget",
            )
        payload = frame.json_object()
        if self._pending_turn_complete is not None and frame.kind != "TELEMETRY":
            raise relay.WireProtocolError(
                "TURN_PROOF_PENDING",
                "only telemetry is accepted while completion proof is staged",
            )
        handler = {
            "MODEL_REQUEST": self._model_request,
            "APPROVAL_REQUEST": self._approval_request,
            "TOOL_EVENT": self._tool_event,
            "TURN_COMPLETE": self._turn_complete,
            "CONTROL_RESULT": self._control_result,
            "TELEMETRY": self._guest_telemetry,
            "SESSION_CLOSED": self._session_closed,
            "WORKSPACE_REQUEST": self._workspace_request,
            **(
                {
                    "TASK_EVENT": self._task_event,
                }
                if self.guest_profile == "nexus"
                else {}
            ),
        }[frame.kind]
        handler(payload)

    def poll_turn_proof(self) -> bool:
        pending = self._pending_turn_complete
        if pending is None:
            return False
        if time.monotonic() < pending[2]:
            return False
        self._pending_turn_complete = None
        raise relay.WireProtocolError(
            "TURN_PROOF_TIMEOUT", "kernel identity proof did not arrive before the deadline"
        )

    def _task_proof_waits_only_for_identity(self) -> bool:
        if self._nexus_task_ledger is None:
            return False
        snapshot = self._nexus_task_ledger.snapshot()
        return (
            snapshot.active
            and not snapshot.sealed
            and snapshot.task_count > 0
            and snapshot.delivered_tool_count == snapshot.settled_tool_count
            and all(task.terminal_event for task in snapshot.tasks)
            and any(not task.identity_verified for task in snapshot.tasks)
        )

    def _retry_pending_turn_complete(self) -> None:
        pending = self._pending_turn_complete
        if pending is None:
            return
        payload, _status, _deadline = pending
        self._pending_turn_complete = None
        try:
            self._turn_complete(payload)
        except relay.WireProtocolError:
            if self._task_proof_waits_only_for_identity():
                self._pending_turn_complete = pending
                return
            raise

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
                receipt = getattr(completion.error, "receipt", None)
                failure_proof: dict[str, object] | None = None
                if self.guest_profile == "nexus" and isinstance(
                    receipt, relay.ProviderReceipt
                ):
                    failure_proof = {
                        "type": "provider_result",
                        **envelope,
                        "status": "error",
                        "code": _model_error_code(completion.error.code),
                        "generation": completion.generation,
                        "provider": self.provider_name,
                        "model": completion.model,
                        "request_sha256": completion.request_sha256,
                        "raw_guest_request_sha256": completion.raw_guest_request_sha256,
                        "history_bindings": [
                            dict(binding) for binding in completion.history_bindings
                        ],
                        "request_contains_user": True,
                        "user_message_index": completion.user_message_index,
                        "user_content_sha256": turn.user_content_sha256,
                        "user_bytes": turn.user_bytes,
                        **_provider_receipt_fields(receipt),
                    }
                self._send_model_error(
                    envelope,
                    _nexus_model_error(completion.error)
                    if self.guest_profile == "nexus"
                    else completion.error,
                )
                if failure_proof is not None:
                    self._controller(failure_proof)
                    self._telemetry(
                        {"event": "provider_result", **failure_proof}
                    )
                continue
            assert completion.reply is not None
            if completion.final_only and (
                completion.reply.type != "final"
                or _is_dsml_tool_calls_markup(completion.reply.content)
            ):
                self._send_model_error(
                    envelope,
                    relay.ProviderError(
                        "TOOL_CHOICE_MISMATCH",
                        "provider did not return a usable final answer in the Nexus final-only slot",
                        retryable=True,
                    ),
                )
                continue
            if (
                self.guest_profile == "nexus"
                and completion.reply.type == "tool_use"
                and completion.reply.tool not in NEXUS_TOOL_NAMES
            ):
                self._send_model_error(
                    envelope,
                    relay.ProviderError(
                        "UNADVERTISED_TOOL",
                        "provider selected a tool outside the Nexus contract",
                        retryable=True,
                    ),
                )
                continue
            if (
                self.guest_profile == "nexus"
                and completion.reply.type == "final"
                and any(
                    not bool(entry.get("settled"))
                    for entry in self._nexus_tool_ledger.values()
                )
            ):
                self._send_model_error(
                    envelope,
                    relay.ProviderError(
                        "TOOL_UNSETTLED",
                        "provider final arrived before Guest tool settlement",
                        retryable=True,
                    ),
                )
                continue
            try:
                response = completion.reply.wire_payload(
                    completion.corr_id,
                    max_tool_arguments=self.max_tool_arguments,
                    max_tool_argument_string_bytes=(
                        self.max_tool_argument_string_bytes
                    ),
                    max_final_bytes=self.max_final_bytes,
                )
                if (
                    self.guest_profile == "nexus"
                    and completion.reply.type == "tool_use"
                ):
                    _validate_nexus_tool_arguments(
                        str(completion.reply.tool), response["arguments"]
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
                    relay.ProviderError(
                        caught.code,
                        caught.public_message,
                        retryable=True,
                    )
                    if isinstance(caught, relay.ProviderError)
                    else relay.ProviderError(
                        "MODEL_RESPONSE_INVALID",
                        "provider reply exceeds the negotiated Guest response contract",
                        retryable=True,
                    )
                )
                self._send_model_error(envelope, error)
                continue
            self.tx_seq += 1
            self.send_line(response_line)
            if self.guest_profile == "nexus":
                # Nexus rounds are decisions actually delivered to the Guest.
                # Retryable MODEL_ERROR frames have their own independent budget.
                turn.rounds += 1
            commit_model_reply = getattr(
                self.provider, "commit_model_reply", None
            )
            if callable(commit_model_reply):
                commit_model_reply(completion.corr_id, completion.reply)
            self._last_model_response_corr = completion.corr_id
            self._last_final_response = (
                (completion.corr_id, str(response["content"]))
                if completion.reply.type == "final"
                else None
            )
            if self.guest_profile == "nexus" and completion.reply.type == "tool_use":
                arguments_canonical = relay.canonical_json_bytes(
                    response["arguments"]
                ).decode("utf-8")
                assert self._nexus_task_ledger is not None
                try:
                    self._nexus_task_ledger.record_delivered_tool(
                        completion.corr_id,
                        str(completion.reply.tool),
                        arguments_canonical=arguments_canonical,
                    )
                except nexus_task_ledger.NexusTaskLedgerError as error:
                    raise _nexus_ledger_wire_error(error) from None
                self._nexus_tool_ledger[completion.corr_id] = {
                    "tool": str(completion.reply.tool),
                    "arguments_canonical": arguments_canonical,
                    "arguments": json.loads(arguments_canonical),
                    "settled": False,
                    "round_limit_pending": turn.rounds == self.max_rounds,
                }
            elif self.guest_profile == "nexus" and completion.reply.type == "final":
                assert self._nexus_task_ledger is not None
                try:
                    self._nexus_task_ledger.freeze_provider_final(completion.corr_id)
                except nexus_task_ledger.NexusTaskLedgerError as error:
                    raise _nexus_ledger_wire_error(error) from None
                self._last_final_request_sha256 = completion.request_sha256
                self._nexus_final_frozen = True
            response_sha256 = hashlib.sha256(
                relay.canonical_json_bytes(response_payload)
            ).hexdigest()
            proof: dict[str, object] = {}
            if self.guest_profile == "nexus":
                proof = {
                    "generation": completion.generation,
                    "provider": self.provider_name,
                    "model": completion.model,
                    "transport": self.provider_transport,
                    "adapter_success": True,
                    "request_sha256": completion.request_sha256,
                    "raw_guest_request_sha256": completion.raw_guest_request_sha256,
                    "history_bindings": [
                        dict(binding) for binding in completion.history_bindings
                    ],
                    "request_contains_user": True,
                    "user_message_index": completion.user_message_index,
                    "response_sha256": response_sha256,
                    "user_content_sha256": turn.user_content_sha256,
                    "user_bytes": turn.user_bytes,
                }
                if self.endpoint_origin:
                    proof["endpoint_origin"] = self.endpoint_origin
                receipt = completion.reply.receipt
                if receipt is not None:
                    proof.update(_provider_receipt_fields(receipt))
                if completion.reply.type == "final":
                    proof["final_request_sha256"] = self._last_final_request_sha256
                    self._last_final_corr_id = completion.corr_id
                    self._last_final_response_sha256 = response_sha256
                    self._last_final_provider_proof_sha256 = hashlib.sha256(
                        relay.canonical_json_bytes(proof)
                    ).hexdigest()
                    proof["final_response_sha256"] = response_sha256
                    proof["provider_proof_sha256"] = (
                        self._last_final_provider_proof_sha256
                    )
            public: dict[str, object] = {
                "type": "model_response",
                **envelope,
                **proof,
            }
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
            if proof:
                self._telemetry(
                    {
                        "event": "provider_result",
                        **envelope,
                        "status": completion.reply.type,
                        **proof,
                    }
                )
            self._telemetry(
                {
                    "event": "model_response",
                    **envelope,
                    "status": completion.reply.type,
                    **({"tool": completion.reply.tool} if completion.reply.tool else {}),
                    **proof,
                }
            )

    def _send_model_error(
        self, envelope: Mapping[str, object], error: relay.RelayError
    ) -> None:
        corr_id = _positive_u64(envelope.get("corr_id"), "corr_id")
        error_code = _model_error_code(error.code)
        turn = self.active
        if self.guest_profile == "nexus":
            if turn is None:
                raise relay.WireProtocolError(
                    "NO_TURN", "Nexus model error has no active user turn"
                )
            if error.retryable and turn.retries >= self.max_retries:
                raise relay.WireProtocolError(
                    "RETRY_LIMIT", "Nexus model retry limit is exhausted"
                )
        self._send(
            "MODEL_ERROR",
            {
                **envelope,
                "type": "error",
                "code": error_code,
                "message": _model_error_message(error.public_message),
                "retryable": error.retryable,
            },
        )
        if self.guest_profile == "nexus":
            assert turn is not None
            if error.retryable:
                turn.retries += 1
                cause = (
                    "round_limit" if turn.retries == self.max_retries else ""
                )
            else:
                cause = "provider_fatal"
        else:
            cause = (
                "round_limit"
                if error.retryable
                and turn is not None
                and turn.rounds == self.max_rounds
                else ("provider_fatal" if not error.retryable else "")
            )
        if self._nexus_task_ledger is not None:
            try:
                # The proof ledger records only outcomes that were actually
                # delivered to the Guest.  Fatal/round-limit authorization is
                # armed after that per-correlation outcome is committed.
                self._nexus_task_ledger.record_model_error(
                    corr_id, retryable=error.retryable
                )
                if cause:
                    self._nexus_task_ledger.begin_termination(corr_id, cause)
            except nexus_task_ledger.NexusTaskLedgerError as ledger_error:
                raise _nexus_ledger_wire_error(ledger_error) from None
        self._controller({"type": "model_error", **envelope, "code": error_code})
        self._telemetry({"event": "model_error", **envelope, "status": error_code})

    def status(self) -> dict[str, object]:
        active = self.active
        value: dict[str, object] = {
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
        if self.guest_profile == "nexus":
            value["max_rounds"] = self.max_rounds
            value["max_retries"] = self.max_retries
        return value

    def _validate_nexus_history(
        self, records: Mapping[int, Mapping[str, object]]
    ) -> tuple[dict[str, object], ...]:
        if any(
            not bool(entry.get("settled"))
            for entry in self._nexus_tool_ledger.values()
        ):
            raise relay.WireProtocolError(
                "BAD_REQUEST", "previous Nexus tool call is not settled"
            )
        ledger_tail = tuple(self._nexus_tool_ledger)[-NEXUS_HISTORY_TURNS:]
        supplied_corrs = tuple(records)
        history_is_suffix = bool(
            supplied_corrs
            and len(supplied_corrs) <= len(ledger_tail)
            and supplied_corrs == ledger_tail[-len(supplied_corrs):]
        )
        if (ledger_tail and not history_is_suffix) or (
            not ledger_tail and supplied_corrs
        ):
            raise relay.WireProtocolError(
                "BAD_REQUEST",
                "tool history is not the Guest's contiguous retained suffix",
            )
        for corr_id, record in records.items():
            entry = self._nexus_tool_ledger.get(corr_id)
            if entry is None or not bool(entry.get("settled")):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history was not settled by the Host"
                )
            if (
                record.get("tool") != entry.get("tool")
                or record.get("arguments_canonical")
                != entry.get("arguments_canonical")
                or record.get("is_error") != (entry.get("status") != 0)
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history call binding is malformed"
                )
            wrapper = record.get("wrapper")
            if not isinstance(wrapper, Mapping):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history result is malformed"
                )
            scalar_fields: dict[str, object] = {
                "status": entry["status"],
                "value0": entry["value0"],
                "value1": entry["value1"],
                "value2": entry["value2"],
                "result": entry["result"],
            }
            projection_digest = str(entry.get("projection_sha256", ""))
            tool = str(entry["tool"])
            expected_fields = set(scalar_fields)
            expected_wrapper = dict(scalar_fields)
            if projection_digest:
                if tool not in NEXUS_WORKSPACE_TOOLS and tool != "inspect_system":
                    raise relay.WireProtocolError(
                        "BAD_REQUEST", "tool history projection type is unsupported"
                    )
                projection_field = (
                    "model_projection"
                    if tool in NEXUS_WORKSPACE_TOOLS
                    else "runtime_observation"
                )
                expected_fields.add(projection_field)
                content = wrapper.get(projection_field)
                if not isinstance(content, str) or not hmac.compare_digest(
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    projection_digest,
                ):
                    raise relay.WireProtocolError(
                        "BAD_REQUEST", "tool history projection digest is malformed"
                    )
                expected_projection = (
                    entry.get("workspace_result")
                    if tool in NEXUS_WORKSPACE_TOOLS
                    else entry.get("model_projection")
                )
                if content != expected_projection:
                    raise relay.WireProtocolError(
                        "BAD_REQUEST", "tool history changed settled projection bytes"
                    )
                expected_wrapper[projection_field] = expected_projection
                if tool == "inspect_system":
                    expected_fields.add("data_trust")
                    expected_wrapper["data_trust"] = "guest_runtime_untrusted"
            if set(wrapper) != expected_fields or any(
                wrapper.get(key) != value for key, value in scalar_fields.items()
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history result does not match settlement"
                )
            wrapper_digest = hashlib.sha256(
                str(record["wrapper_canonical"]).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(
                wrapper_digest,
                hashlib.sha256(relay.canonical_json_bytes(expected_wrapper)).hexdigest(),
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history result digest is malformed"
                )
            if (tool != "inspect_system" or not projection_digest) and not hmac.compare_digest(
                wrapper_digest, str(entry.get("result_sha256", ""))
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history result settlement is malformed"
                )
            if record.get("projection_sha256", "") != projection_digest:
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "tool history projection binding is malformed"
                )
        return _history_bindings(records)

    def _model_request(self, payload: dict[str, object]) -> None:
        turn = self._match_active(payload)
        if self.guest_profile == "nexus" and self._nexus_final_frozen:
            raise relay.WireProtocolError(
                "BAD_REQUEST", "a final model response already froze this Nexus turn"
            )
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
        raw_guest_request_sha256 = hashlib.sha256(
            relay.canonical_json_bytes(request_payload)
        ).hexdigest()
        provider_payload = request_payload
        context_path: Mapping[str, object] | None = None
        if self.guest_profile == "nexus":
            try:
                provider_payload = nexus_contract.strip_internal_contract_fields(
                    request_payload
                )
            except nexus_contract.NexusContractError as error:
                raise relay.WireProtocolError("BAD_REQUEST", str(error)) from None
            supplied_context_path = request_payload.get("context_path")
            if not isinstance(supplied_context_path, Mapping):
                raise AssertionError("validated Nexus context_path is not an object")
            context_path = supplied_context_path
        request = relay.validate_guest_request(
            provider_payload,
            max_output_tokens=self.max_tokens,
            max_tool_arguments=self.max_tool_arguments,
            max_tool_argument_string_bytes=(
                self.max_tool_argument_string_bytes
            ),
        )
        request_sha256 = hashlib.sha256(relay.canonical_json_bytes(request)).hexdigest()
        history_records = (
            _history_records(request) if self.guest_profile == "nexus" else {}
        )
        history_bindings = (
            self._validate_nexus_history(history_records)
            if self.guest_profile == "nexus"
            else ()
        )
        user_message_index = -1
        if self.guest_profile == "nexus":
            assert context_path is not None
            context_path_projection = _context_path_metadata(context_path)
            if context_path_projection is None:
                raise AssertionError("validated Nexus context_path metadata changed")
            context_turns = context_path.get("turns")
            if not isinstance(context_turns, list):
                raise AssertionError("validated Nexus Context turns are not a list")
            supplied_context_bindings: list[tuple[int, int, int, int, str]] = []
            for context_turn in context_turns:
                if not isinstance(context_turn, Mapping):
                    raise AssertionError("validated Nexus Context turn is not an object")
                supplied_context_bindings.append(
                    (
                        int(context_turn["turn_id"]),
                        int(context_turn["request_id"]),
                        int(context_turn["user_sequence"]),
                        int(context_turn["final_sequence"]),
                        str(context_turn["sha256"]),
                    )
                )
            retained_suffix = (
                self._nexus_completed_turn_bindings[-len(supplied_context_bindings) :]
                if supplied_context_bindings
                else []
            )
            if supplied_context_bindings != retained_suffix:
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "Context path is not the retained suffix of completed turns",
                )
            branch_generation = int(context_path["branch_generation"])
            if (
                turn.context_branch_generation != 0
                and turn.context_branch_generation != branch_generation
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "Context branch changed within the active turn",
                )
            visible_head_sequence = int(context_path["visible_head_sequence"])
            if visible_head_sequence < turn.context_visible_head_sequence:
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "visible Context head moved backwards within the active turn",
                )
            current_user_sequence = int(context_path["current_user_sequence"])
            if (
                turn.context_user_sequence != 0
                and turn.context_user_sequence != current_user_sequence
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "current user changed within the active Context path",
                )
            context_start_sequence = turn.context_start_sequence
            context_start_known = turn.context_start_known
            if not context_start_known:
                context_start_sequence = current_user_sequence - 1
                context_start_known = True
            elif current_user_sequence <= context_start_sequence:
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "current user Context node does not follow the pinned turn-start head",
                )
            messages = request.get("messages", [])
            assert isinstance(messages, list)
            user_message_index = 2 * len(context_turns)
            current_message = messages[user_message_index]
            current_content = (
                current_message.get("content")
                if isinstance(current_message, Mapping)
                and current_message.get("role") == "user"
                else None
            )
            if (
                not isinstance(current_content, str)
                or not hmac.compare_digest(
                    hashlib.sha256(current_content.encode("utf-8")).hexdigest(),
                    turn.user_content_sha256,
                )
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "model request does not bind the current user message",
                )
            context_index = user_message_index + 1
            if context_index >= len(messages):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "model request is missing its runtime context"
                )
            context_message = messages[context_index]
            context_content = (
                context_message.get("content")
                if isinstance(context_message, Mapping)
                and context_message.get("role") == "user"
                else None
            )
            if (
                not isinstance(context_content, str)
                or not context_content.startswith(NEXUS_CONTROL_CONTEXT_PREFIX)
                or any(
                    isinstance(message, Mapping)
                    and message.get("role") == "user"
                    for message in messages[context_index + 1 :]
                )
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "model request runtime context is malformed"
                )
            if "tool_choice" in request_payload or any(
                key in request_payload for key in ("temperature", "stop")
            ):
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "Nexus autonomy parameters are Host-pinned"
                )
        requested_model = request.get("model")
        if self.guest_profile == "nexus":
            if "model" in request and requested_model != self.model_name:
                raise relay.WireProtocolError(
                    "BAD_REQUEST", "Nexus model does not match the Host configuration"
                )
            if request.get("max_tokens") != self.max_tokens:
                raise relay.WireProtocolError(
                    "BAD_REQUEST",
                    "Nexus max_tokens does not match the negotiated Host policy",
                )
        model = (
            str(requested_model)
            if isinstance(requested_model, str) and requested_model
            else self.model_name
        )
        if (
            self.guest_profile == "nexus"
            and not turn.cancelled
            and (
                turn.rounds >= self.max_rounds
                or turn.retries >= self.max_retries
                or turn.attempts >= self.max_rounds + self.max_retries
            )
        ):
            raise relay.WireProtocolError(
                "ROUND_LIMIT", "model decision or retry limit is exhausted"
            )
        if self._nexus_task_ledger is not None:
            try:
                self._nexus_task_ledger.record_model_request(corr_id)
                if self._nexus_cancel_pending:
                    self._nexus_task_ledger.begin_cancel(corr_id)
                    self._nexus_cancel_pending = False
            except nexus_task_ledger.NexusTaskLedgerError as error:
                raise _nexus_ledger_wire_error(error) from None
        if self.guest_profile == "nexus":
            turn.context_start_sequence = context_start_sequence
            turn.context_start_known = context_start_known
            if self._nexus_context_head_known:
                if self._nexus_context_head_sequence != context_start_sequence:
                    raise AssertionError("active Context turn-start head changed")
            else:
                self._nexus_context_head_sequence = context_start_sequence
                self._nexus_context_head_known = True
            turn.context_branch_generation = branch_generation
            turn.context_user_sequence = current_user_sequence
            turn.context_visible_head_sequence = visible_head_sequence
        self._active_last_corr = corr_id
        self._last_corr = corr_id
        # Only a response to this newer request may authorize a later tool.
        # An approval for the preceding response is stale from this point on.
        self._last_model_response_corr = 0
        self._last_final_response = None
        if self.guest_profile == "nexus":
            # The Guest consumes an attempt before it emits MODEL_REQUEST.  A
            # request racing with an already-queued CANCEL is still an
            # authenticated attempt even though the Host must not call the
            # provider for it.
            turn.attempts += 1
            request_round = turn.rounds + 1
            request_attempt = turn.attempts
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
                    **(
                        {"round": request_round, "attempt": request_attempt}
                        if self.guest_profile == "nexus"
                        else {}
                    ),
                    "reason": "turn_cancelled",
                    "request_sha256": request_sha256,
                    "raw_guest_request_sha256": raw_guest_request_sha256,
                    "history_bindings": [
                        dict(binding) for binding in history_bindings
                    ],
                    **(
                        {"context_path": copy.deepcopy(context_path_projection)}
                        if self.guest_profile == "nexus"
                        else {}
                    ),
                    "request_contains_user": True,
                    "user_message_index": user_message_index,
                    "generation": turn.generation,
                    "user_content_sha256": turn.user_content_sha256,
                    "user_bytes": turn.user_bytes,
                }
            )
            self._telemetry(
                {
                    "event": "model_request_after_cancel_dropped",
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "corr_id": corr_id,
                    **(
                        {"round": request_round, "attempt": request_attempt}
                        if self.guest_profile == "nexus"
                        else {}
                    ),
                    "state": "CANCELLING",
                    "request_sha256": request_sha256,
                    "raw_guest_request_sha256": raw_guest_request_sha256,
                    "history_bindings": [
                        dict(binding) for binding in history_bindings
                    ],
                    **(
                        {"context_path": copy.deepcopy(context_path_projection)}
                        if self.guest_profile == "nexus"
                        else {}
                    ),
                    "request_contains_user": True,
                    "user_message_index": user_message_index,
                    "generation": turn.generation,
                    "user_content_sha256": turn.user_content_sha256,
                    "user_bytes": turn.user_bytes,
                }
            )
            return
        if (
            self.guest_profile == "nexus"
            and len(self._provider_inflight) >= NEXUS_MAX_PROVIDER_INFLIGHT
        ):
            raise relay.WireProtocolError(
                "PROVIDER_BUSY",
                "provider isolation capacity is exhausted",
            )
        if self.guest_profile == "nexus":
            if (
                turn.rounds >= self.max_rounds
                or turn.retries >= self.max_retries
            ):
                raise relay.WireProtocolError(
                    "ROUND_LIMIT", "model decision or retry limit is exhausted"
                )
        else:
            if turn.rounds >= self.max_rounds:
                raise relay.WireProtocolError(
                    "ROUND_LIMIT", "model round limit is exhausted"
                )
            turn.rounds += 1
            request_round = turn.rounds
            request_attempt = 0
        final_only = (
            self.guest_profile == "nexus"
            and not isinstance(self.provider, relay.ReplayProvider)
            and turn.rounds == self.max_rounds - 1
        )
        provider_request_base = request
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
                "round": request_round,
                **(
                    {"attempt": request_attempt}
                    if self.guest_profile == "nexus"
                    else {}
                ),
                "request_sha256": request_sha256,
                "raw_guest_request_sha256": raw_guest_request_sha256,
                "history_bindings": [
                    dict(binding) for binding in history_bindings
                ],
                **(
                    {"context_path": copy.deepcopy(context_path_projection)}
                    if self.guest_profile == "nexus"
                    else {}
                ),
                "request_contains_user": True,
                "user_message_index": user_message_index,
                "generation": turn.generation,
                "user_content_sha256": turn.user_content_sha256,
                "user_bytes": turn.user_bytes,
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
                "raw_guest_request_sha256": raw_guest_request_sha256,
                "history_bindings": [
                    dict(binding) for binding in history_bindings
                ],
                **(
                    {"context_path": copy.deepcopy(context_path_projection)}
                    if self.guest_profile == "nexus"
                    else {}
                ),
                "request_contains_user": True,
                "user_message_index": user_message_index,
                "generation": turn.generation,
                "user_content_sha256": turn.user_content_sha256,
                "user_bytes": turn.user_bytes,
            }
        )
        def worker() -> None:
            reply: relay.ModelReply | None = None
            error: relay.RelayError | None = None
            try:
                provider_request = copy.deepcopy(provider_request_base)
                if final_only:
                    provider_request["_nexus_final_only"] = True
                    provider_request["tools"] = []
                    provider_request.pop("tool_choice", None)
                    system = provider_request.get("system")
                    if not isinstance(system, str):
                        raise relay.ProviderError(
                            "BAD_REQUEST", "Nexus final-only request lacks system policy"
                        )
                    provider_request["system"] = (
                        system + "\n\n" + NEXUS_FINAL_ONLY_SYSTEM_SUFFIX
                    )
                reply = self.provider.complete(
                    provider_request,
                    deadline_monotonic=(
                        time.monotonic()
                        + (
                            NEXUS_INTERACTIVE_PROVIDER_TIMEOUT_SECONDS
                            if self.guest_profile == "nexus"
                            else INTERACTIVE_PROVIDER_TIMEOUT_SECONDS
                        )
                    ),
                )
                if not isinstance(reply, relay.ModelReply):
                    raise relay.ProviderError(
                        "BAD_PROVIDER_RESPONSE", "provider returned an invalid reply"
                    )
            except relay.RelayError as caught:
                error = caught
            except Exception:
                error = relay.ProviderError(
                    (
                        "PROVIDER_ADAPTER_ERROR"
                        if self.guest_profile == "nexus"
                        else "PROVIDER_FAILURE"
                    ),
                    "provider adapter failed unexpectedly",
                    retryable=self.guest_profile == "nexus",
                )
            except BaseException:
                error = relay.ProviderError(
                    "PROVIDER_FAILURE", "provider adapter failed unexpectedly"
                )
            self._provider_results.put(
                ProviderCompletion(
                    generation=generation,
                    turn_id=turn.turn_id,
                    request_id=turn.request_id,
                    corr_id=corr_id,
                    request_sha256=request_sha256,
                    raw_guest_request_sha256=raw_guest_request_sha256,
                    history_bindings=history_bindings,
                    user_message_index=user_message_index,
                    model=model,
                    final_only=final_only,
                    reply=reply,
                    error=error,
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
        if self.guest_profile == "nexus":
            raise relay.WireProtocolError(
                "BAD_APPROVAL", "Nexus autonomy contract has no approval tool"
            )
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
        if "tool_id" in payload:
            binding["tool_id"] = _positive_u64(payload["tool_id"], "tool_id")
        if "arguments" in payload:
            if not isinstance(payload["arguments"], dict):
                raise relay.WireProtocolError("BAD_APPROVAL", "approval arguments are malformed")
            binding["arguments"] = payload["arguments"]
        if "canonical_arguments" in payload:
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
        expires = payload.get("expires_tick", payload.get("expiry_tick"))
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
        binding_fields = APPROVAL_BINDING_FIELDS
        payload = {key: binding[key] for key in binding_fields}
        payload["decision"] = decision
        self._send("APPROVAL_DECISION", payload)
        self.pending_approval = None
        self._approval_deadline = 0.0
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

    def _workspace_request(self, payload: dict[str, object]) -> None:
        code = "BAD_WORKSPACE_EVENT"
        if self.guest_profile != "nexus":
            raise relay.WireProtocolError(
                code, "workspace exchange is only valid for the Nexus profile"
            )
        if set(payload) != set(NEXUS_WORKSPACE_REQUEST_FIELDS):
            raise relay.WireProtocolError(code, "workspace request fields are malformed")
        turn = self._match_active(payload)
        if self._nexus_final_frozen and not turn.cancelled:
            raise relay.WireProtocolError(
                code, "workspace request arrived outside active tool execution"
            )
        version = _bounded_int(
            payload["version"], "version", code=code, minimum=1, maximum=1
        )
        corr_id = _bounded_int(
            payload["corr_id"], "corr_id", code=code, minimum=1,
            maximum=relay.MAX_SEQUENCE,
        )
        task_id = _bounded_int(
            payload["task_id"], "task_id", code=code, minimum=1,
            maximum=(1 << 32) - 1,
        )
        attempt = _bounded_int(
            payload["attempt"], "attempt", code=code, minimum=1,
            maximum=nexus_task_ledger.MAX_WORKSPACE_ATTEMPTS,
        )
        tool = _safe_text(payload["tool"], "tool", code=code, maximum=64)
        operation = _safe_text(
            payload["operation"], "operation", code=code, maximum=16
        )
        if operation not in NEXUS_WORKSPACE_OPERATIONS:
            raise relay.WireProtocolError(code, "workspace operation is unsupported")
        allowed_operations = {
            "search_files": frozenset(("manifest", "search")),
            "read_file": frozenset(("manifest", "read")),
        }
        if operation not in allowed_operations.get(tool, frozenset()):
            raise relay.WireProtocolError(
                code, "workspace operation does not match the delivered tool"
            )
        entry = self._nexus_tool_ledger.get(corr_id)
        if (
            entry is None
            or entry.get("tool") != tool
            or bool(entry.get("settled"))
            or corr_id
            != (
                self._active_last_corr
                if turn.cancelled
                else self._last_model_response_corr
            )
        ):
            raise relay.WireProtocolError(
                code, "workspace request has no delivered unsettled tool"
            )
        generation = _safe_text(
            payload["workspace_generation"],
            "workspace_generation",
            code=code,
            maximum=64,
            empty=True,
        )
        if generation:
            _digest(generation, "workspace_generation", code=code)
        arguments_sha256 = _digest(
            payload["arguments_sha256"], "arguments_sha256", code=code
        )
        arguments_value = payload["arguments"]
        if not isinstance(arguments_value, dict):
            raise relay.WireProtocolError(code, "workspace arguments must be an object")
        arguments = dict(arguments_value)
        canonical_arguments = relay.canonical_json_bytes(arguments)
        if not hmac.compare_digest(
            hashlib.sha256(canonical_arguments).hexdigest(), arguments_sha256
        ):
            raise relay.WireProtocolError(
                code, "workspace arguments do not match their digest"
            )
        public_arguments = entry.get("arguments")
        if not isinstance(public_arguments, Mapping):
            raise relay.WireProtocolError(
                code, "delivered workspace tool arguments are unavailable"
            )

        if operation == "manifest":
            if set(arguments) != {"cursor", "limit"}:
                raise relay.WireProtocolError(
                    code, "manifest arguments are malformed"
                )
            cursor = _bounded_int(
                arguments["cursor"], "cursor", code=code, minimum=0,
                maximum=(1 << 32) - 1,
            )
            limit = _bounded_int(
                arguments["limit"], "limit", code=code, minimum=1,
                maximum=workspace.MAX_MANIFEST_PAGE,
            )
            if cursor == 0 and generation:
                raise relay.WireProtocolError(
                    code, "manifest restart must refresh the workspace"
                )
            if cursor != 0 and not generation:
                raise relay.WireProtocolError(
                    code, "non-initial manifest page lacks a generation"
                )
            if cursor != 0 and entry.get("workspace_next_cursor") != cursor:
                raise relay.WireProtocolError(
                    code, "manifest cursor skipped or repeated a Catalog page"
                )
            pending_candidates = entry.get("workspace_pending_candidates", [])
            if tool == "search_files" and pending_candidates:
                raise relay.WireProtocolError(
                    code, "next manifest page preceded its candidate search"
                )
        elif operation == "search":
            if set(arguments) != {"query", "candidates"}:
                raise relay.WireProtocolError(code, "search arguments are malformed")
            if not generation:
                raise relay.WireProtocolError(code, "search lacks a generation")
            query = _safe_text(
                arguments["query"],
                "query",
                code=code,
                maximum=workspace.MAX_QUERY_BYTES,
                empty=True,
            )
            if query != public_arguments.get("query"):
                raise relay.WireProtocolError(
                    code, "search changed the delivered query"
                )
            try:
                candidates = workspace.WorkspaceReader._validate_candidates(
                    arguments["candidates"]
                )
            except (TypeError, ValueError) as error:
                raise relay.WireProtocolError(
                    code, "search candidates are malformed"
                ) from error
            prefix_value = public_arguments.get("path_prefix", "")
            if not isinstance(prefix_value, str):
                raise relay.WireProtocolError(
                    code, "delivered search prefix is malformed"
                )
            if any(prefix_value and not path.startswith(prefix_value) for _, path, _ in candidates):
                raise relay.WireProtocolError(
                    code, "search candidate is outside the delivered prefix"
                )
            expected_candidates = entry.get("workspace_pending_candidates")
            canonical_candidates = [
                {"object_id": object_id, "path": path, "revision": revision}
                for object_id, path, revision in candidates
            ]
            if (
                not isinstance(expected_candidates, list)
                or canonical_candidates != expected_candidates
            ):
                raise relay.WireProtocolError(
                    code, "search candidates do not cover the Catalog page"
                )
        else:
            if set(arguments) != {
                "object_id",
                "path",
                "revision",
                "start_line",
                "max_lines",
            }:
                raise relay.WireProtocolError(code, "read arguments are malformed")
            if not generation:
                raise relay.WireProtocolError(code, "read lacks a generation")
            try:
                read_candidates = workspace.WorkspaceReader._validate_candidates(
                    [
                        {
                            "object_id": arguments["object_id"],
                            "path": arguments["path"],
                            "revision": arguments["revision"],
                        }
                    ]
                )
            except (TypeError, ValueError) as error:
                raise relay.WireProtocolError(
                    code, "read object binding is malformed"
                ) from error
            read_binding = {
                "object_id": read_candidates[0][0],
                "path": read_candidates[0][1],
                "revision": read_candidates[0][2],
            }
            manifest_objects = entry.get("workspace_manifest_objects")
            if not isinstance(manifest_objects, list) or read_binding not in manifest_objects:
                raise relay.WireProtocolError(
                    code, "read object was not delivered through the Catalog"
                )
            start_line = _bounded_int(
                arguments["start_line"], "start_line", code=code, minimum=1,
                maximum=(1 << 32) - 1,
            )
            max_lines = _bounded_int(
                arguments["max_lines"], "max_lines", code=code, minimum=1,
                maximum=NEXUS_FILE_READ_MAX_LINES,
            )
            if (
                arguments["path"] != public_arguments.get("path")
                or start_line != public_arguments.get("start_line")
                or max_lines != public_arguments.get("max_lines")
            ):
                raise relay.WireProtocolError(
                    code, "read changed the delivered file request"
                )

        objects_sha256 = _workspace_objects_sha256(arguments)
        if turn.cancelled:
            last_attempt = entry.get("workspace_last_attempt", 0)
            if (
                not isinstance(last_attempt, int)
                or isinstance(last_attempt, bool)
                or attempt != last_attempt + 1
                or "workspace_cancel_drain_attempt" in entry
            ):
                raise relay.WireProtocolError(
                    code, "cancelled workspace request is duplicated or reordered"
                )
            content = "workspace_error=cancelled"
            entry["workspace_cancel_drain_attempt"] = attempt
            result_payload = {
                "version": version,
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "corr_id": corr_id,
                "task_id": task_id,
                "tool": tool,
                "operation": operation,
                "attempt": attempt,
                "arguments_sha256": arguments_sha256,
                "workspace_generation": generation or ("0" * 64),
                "status": "error",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            self._send("WORKSPACE_RESULT", result_payload)
            self._telemetry(
                {
                    "event": "workspace_request_after_cancel_drained",
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "corr_id": corr_id,
                    "task_id": task_id,
                    "tool": tool,
                    "operation": operation,
                    "attempt": attempt,
                    "arguments_sha256": arguments_sha256,
                },
                source="host",
            )
            return
        assert self._nexus_task_ledger is not None
        try:
            self._nexus_task_ledger.record_workspace_request(
                corr_id,
                task_id=task_id,
                tool=tool,
                operation=operation,
                attempt=attempt,
                workspace_generation=generation,
                arguments_sha256=arguments_sha256,
                objects_sha256=objects_sha256,
                manifest_cursor=cursor if operation == "manifest" else 0,
            )
        except nexus_task_ledger.NexusTaskLedgerError as error:
            raise _nexus_ledger_wire_error(error) from None
        entry["workspace_last_attempt"] = attempt
        request_event = {
            "type": "workspace_request",
            "turn_id": turn.turn_id,
            "request_id": turn.request_id,
            "corr_id": corr_id,
            "task_id": task_id,
            "tool": tool,
            "operation": operation,
            "attempt": attempt,
            "workspace_generation": generation,
            "arguments_sha256": arguments_sha256,
            "objects_sha256": objects_sha256,
            "manifest_cursor": cursor if operation == "manifest" else 0,
        }
        self._controller(request_event)
        self._telemetry(
            {"event": "workspace_request", **request_event}, source="guest"
        )

        if self.workspace_reader is None:
            operation_result = workspace.WorkspaceOperationResult(
                "error",
                generation if generation else "0" * 64,
                "workspace_error=workspace_not_configured",
            )
        else:
            try:
                if operation == "manifest":
                    operation_result = self.workspace_reader.manifest(
                        generation, cursor, limit
                    )
                elif operation == "search":
                    operation_result = self.workspace_reader.search_candidates(
                        generation, query, arguments["candidates"]
                    )
                else:
                    operation_result = self.workspace_reader.read_versioned(
                        generation,
                        str(arguments["object_id"]),
                        str(arguments["path"]),
                        str(arguments["revision"]),
                        start_line,
                        max_lines,
                    )
            except Exception:
                operation_result = workspace.WorkspaceOperationResult(
                    "error",
                    generation if generation else "0" * 64,
                    "workspace_error=host_workspace_failure",
                )
        result_status = operation_result.status
        if result_status not in NEXUS_WORKSPACE_STATUSES:
            result_status = "error"
            content = "workspace_error=invalid_host_result"
        else:
            maximum = (
                NEXUS_WORKSPACE_MANIFEST_MAX_BYTES
                if operation == "manifest"
                else NEXUS_WORKSPACE_RESULT_MAX_BYTES
            )
            content = _bounded_workspace_result(
                operation_result.content, maximum=maximum
            )
            if content != operation_result.content:
                result_status = "error"
        result_generation = operation_result.workspace_generation
        if relay.WIRE_DIGEST_RE.fullmatch(result_generation) is None:
            result_generation = generation if generation else "0" * 64
            result_status = "error"
            content = "workspace_error=invalid_host_result"
        if result_status == "stale":
            content = ""
        source_objects_sha256 = objects_sha256
        manifest_next_cursor = 0
        manifest_eof_result = False
        if result_status == "ok":
            try:
                if operation == "manifest":
                    parsed_cursor, next_cursor, manifest_eof, manifest_entries = (
                        _parse_workspace_manifest_content(content)
                    )
                    if parsed_cursor != cursor or len(manifest_entries) > limit:
                        raise ValueError("workspace manifest changed its request")
                    if tool == "search_files":
                        projected_search_arguments = {
                            "query": public_arguments.get("query", ""),
                            "candidates": manifest_entries,
                        }
                        if (
                            len(
                                relay.canonical_json_bytes(
                                    projected_search_arguments
                                )
                            )
                            > workspace.MAX_WORKSPACE_ARGUMENT_BYTES
                        ):
                            raise ValueError(
                                "workspace manifest exceeds Guest search arguments"
                            )
                    source_objects_sha256 = _workspace_objects_sha256(
                        {"candidates": manifest_entries}
                    )
                    manifest_next_cursor = next_cursor
                    manifest_eof_result = manifest_eof
                    manifest_objects = (
                        []
                        if not generation
                        else entry.get("workspace_manifest_objects")
                    )
                    if not isinstance(manifest_objects, list):
                        raise ValueError("workspace manifest object set is malformed")
                    existing_object_ids = {
                        str(item.get("object_id"))
                        for item in manifest_objects
                        if isinstance(item, Mapping)
                    }
                    existing_paths = {
                        str(item.get("path"))
                        for item in manifest_objects
                        if isinstance(item, Mapping)
                    }
                    page_object_ids = [
                        str(item["object_id"]) for item in manifest_entries
                    ]
                    page_paths = [str(item["path"]) for item in manifest_entries]
                    if (
                        len(set(page_object_ids)) != len(page_object_ids)
                        or len(set(page_paths)) != len(page_paths)
                        or any(
                            object_id in existing_object_ids
                            for object_id in page_object_ids
                        )
                        or any(path in existing_paths for path in page_paths)
                    ):
                        raise ValueError("workspace manifest repeats an object")
                    if not generation:
                        entry["workspace_source_records"] = []
                        entry["workspace_manifest_objects"] = []
                        entry["workspace_aggregate"] = {
                            "matches": [],
                            "truncated": False,
                            "manifest_eof": False,
                        }
                        for key in (
                            "workspace_result",
                            "workspace_content_sha256",
                            "workspace_source_sha256",
                        ):
                            entry.pop(key, None)
                    aggregate = entry.setdefault(
                        "workspace_aggregate",
                        {
                            "matches": [],
                            "truncated": False,
                            "manifest_eof": False,
                        },
                    )
                    aggregate["manifest_eof"] = manifest_eof
                    aggregate["manifest_entries"] = manifest_entries
                    entry["workspace_next_cursor"] = next_cursor
                    manifest_objects = entry.setdefault(
                        "workspace_manifest_objects", []
                    )
                    assert isinstance(manifest_objects, list)
                    manifest_objects.extend(manifest_entries)
                    if tool == "search_files":
                        prefix = str(public_arguments.get("path_prefix", ""))
                        entry["workspace_pending_candidates"] = [
                            candidate
                            for candidate in manifest_entries
                            if not prefix or str(candidate["path"]).startswith(prefix)
                        ]
                elif operation == "search":
                    parsed_query, candidate_count, page_truncated, page_matches = (
                        _parse_workspace_search_content(content)
                    )
                    requested_candidates = arguments["candidates"]
                    assert isinstance(requested_candidates, list)
                    if (
                        parsed_query != query
                        or candidate_count != len(requested_candidates)
                    ):
                        raise ValueError("workspace search changed its request")
                    candidate_bindings = {
                        (
                            str(candidate["object_id"]),
                            str(candidate["path"]),
                            str(candidate["revision"]),
                        )
                        for candidate in requested_candidates
                        if isinstance(candidate, Mapping)
                    }
                    if any(
                        (
                            str(match["object_id"]),
                            str(match["path"]),
                            str(match["revision"]),
                        )
                        not in candidate_bindings
                        for match in page_matches
                    ):
                        raise ValueError(
                            "workspace search returned an unrequested object"
                        )
                    aggregate = entry.get("workspace_aggregate")
                    if not isinstance(aggregate, dict):
                        raise ValueError("workspace search lacks a manifest page")
                    matches = aggregate.get("matches")
                    if not isinstance(matches, list):
                        raise ValueError("workspace search aggregate is malformed")
                    remaining = max(0, workspace.MAX_RESULTS - len(matches))
                    if len(page_matches) > remaining:
                        aggregate["truncated"] = True
                    matches.extend(page_matches[:remaining])
                    aggregate["truncated"] = bool(
                        aggregate.get("truncated") or page_truncated
                    )
                    entry["workspace_pending_candidates"] = []
                    if len(matches) == workspace.MAX_RESULTS and not bool(
                        aggregate.get("manifest_eof")
                    ):
                        aggregate["truncated"] = True
                else:
                    if content.startswith("workspace_error="):
                        if content not in {
                            "workspace_error=binary_file",
                            "workspace_error=file_too_large",
                            "workspace_error=start_line_out_of_range",
                            "workspace_error=line_too_large",
                        }:
                            raise ValueError("workspace read error is not model-visible")
                    elif not content.startswith("workspace_read\n"):
                        raise ValueError("workspace read result is malformed")
            except (TypeError, ValueError, workspace._WorkspaceInputError):
                result_status = "error"
                content = "workspace_error=invalid_host_result"
                source_objects_sha256 = objects_sha256
                manifest_next_cursor = 0
                manifest_eof_result = False
        if result_status in ("stale", "error"):
            for key in (
                "workspace_result",
                "workspace_content_sha256",
                "workspace_source_sha256",
            ):
                entry.pop(key, None)
            if result_status == "stale":
                entry["workspace_source_records"] = []
                entry.pop("workspace_aggregate", None)
                entry["workspace_pending_candidates"] = []
                entry.pop("workspace_next_cursor", None)
                entry.pop("workspace_manifest_objects", None)
        content_bytes = len(content.encode("utf-8"))
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        result_payload = {
            "version": version,
            "turn_id": turn.turn_id,
            "request_id": turn.request_id,
            "corr_id": corr_id,
            "task_id": task_id,
            "tool": tool,
            "operation": operation,
            "attempt": attempt,
            "arguments_sha256": arguments_sha256,
            "workspace_generation": result_generation,
            "status": result_status,
            "content": content,
            "content_sha256": content_sha256,
        }
        try:
            self._nexus_task_ledger.record_workspace_result(
                corr_id,
                task_id=task_id,
                tool=tool,
                operation=operation,
                attempt=attempt,
                workspace_generation=result_generation,
                arguments_sha256=arguments_sha256,
                result_objects_sha256=source_objects_sha256,
                status=result_status,
                content_bytes=content_bytes,
                content_sha256=content_sha256,
                manifest_cursor=cursor if operation == "manifest" else 0,
                manifest_next_cursor=manifest_next_cursor,
                manifest_eof=manifest_eof_result,
                content=content,
            )
        except nexus_task_ledger.NexusTaskLedgerError as error:
            raise _nexus_ledger_wire_error(error) from None
        if result_status == "ok":
            source_records = entry.setdefault("workspace_source_records", [])
            if not isinstance(source_records, list):
                raise relay.WireProtocolError(
                    code, "workspace source accumulator is malformed"
                )
            source_records.append(
                _workspace_source_record(
                    operation=operation,
                    attempt=attempt,
                    request_generation=generation,
                    result_generation=result_generation,
                    arguments_sha256=arguments_sha256,
                    objects_sha256=source_objects_sha256,
                    content_bytes=content_bytes,
                    content_sha256=content_sha256,
                )
            )
            workspace_source_sha256 = hashlib.sha256(
                "".join(source_records).encode("utf-8")
            ).hexdigest()
            try:
                ledger_source_sha256 = (
                    self._nexus_task_ledger.workspace_source_sha256(corr_id)
                )
            except nexus_task_ledger.NexusTaskLedgerError as error:
                raise _nexus_ledger_wire_error(error) from None
            if not hmac.compare_digest(
                workspace_source_sha256, ledger_source_sha256
            ):
                raise relay.WireProtocolError(
                    code, "workspace source root disagrees with the Task ledger"
                )
            entry["workspace_source_sha256"] = workspace_source_sha256
            if tool == "search_files":
                aggregate = entry.get("workspace_aggregate")
                if not isinstance(aggregate, Mapping):
                    raise relay.WireProtocolError(
                        code, "workspace search aggregate is unavailable"
                    )
                matches = aggregate.get("matches")
                if not isinstance(matches, list):
                    raise relay.WireProtocolError(
                        code, "workspace search matches are unavailable"
                    )
                aggregate_content = _render_workspace_search_aggregate(
                    str(public_arguments.get("query", "")),
                    str(public_arguments.get("path_prefix", "")),
                    matches,
                    bool(aggregate.get("truncated")),
                )
                entry.update(
                    {
                        "workspace_result": aggregate_content,
                        "workspace_content_sha256": hashlib.sha256(
                            aggregate_content.encode("utf-8")
                        ).hexdigest(),
                        "workspace_task_id": task_id,
                    }
                )
            elif operation == "read":
                entry.update(
                    {
                        "workspace_result": content,
                        "workspace_content_sha256": content_sha256,
                        "workspace_task_id": task_id,
                    }
                )
        entry.setdefault("workspace_attempts", []).append(
            {
                "task_id": task_id,
                "operation": operation,
                "attempt": attempt,
                "request_generation": generation,
                "result_generation": result_generation,
                "arguments_sha256": arguments_sha256,
                "objects_sha256": objects_sha256,
                "result_objects_sha256": source_objects_sha256,
                "status": result_status,
                "content_bytes": content_bytes,
                "content_sha256": content_sha256,
            }
        )
        self._send("WORKSPACE_RESULT", result_payload)
        result_event = {
            **request_event,
            "type": "workspace_result",
            "workspace_generation": result_generation,
            "objects_sha256": source_objects_sha256,
            "status": result_status,
            "content_bytes": content_bytes,
            "content_sha256": content_sha256,
            "manifest_next_cursor": manifest_next_cursor,
            "manifest_eof": manifest_eof_result,
        }
        self._controller(result_event)
        self._telemetry({"event": "workspace_result", **result_event})

    def _tool_event(self, payload: dict[str, object]) -> None:
        if self.guest_profile != "nexus":
            if "turn_id" in payload:
                self._match_active(payload)
            event = {**payload, "type": "tool_event"}
            self._controller(event)
            telemetry = dict(payload)
            telemetry.setdefault("event", "tool_event")
            self._telemetry(telemetry, source="guest")
            return
        code = "BAD_TOOL_EVENT"
        if set(payload) != set(NEXUS_TOOL_EVENT_FIELDS):
            raise relay.WireProtocolError(code, "Nexus tool event fields are malformed")
        turn = self._match_active(payload)
        corr_id = _bounded_int(
            payload["corr_id"], "corr_id", code=code, minimum=1,
            maximum=relay.MAX_SEQUENCE,
        )
        settlement_after_cancel = bool(
            self._nexus_task_ledger is not None
            and self._nexus_task_ledger.snapshot().termination_cause
            in ("user_interrupt", "round_limit", "session_error")
            and corr_id in self._nexus_tool_ledger
            and not bool(self._nexus_tool_ledger[corr_id].get("settled"))
        )
        if (turn.cancelled and not settlement_after_cancel) or self._nexus_final_frozen:
            raise relay.WireProtocolError(code, "Nexus tool event arrived outside execution")
        tool = _safe_text(payload["tool"], "tool", code=code, maximum=64)
        entry = self._nexus_tool_ledger.get(corr_id)
        if (
            entry is None
            or entry.get("tool") != tool
            or bool(entry.get("settled"))
        ):
            raise relay.WireProtocolError(code, "Nexus tool event has no pending call")
        status = _bounded_int(
            payload["status"], "status", code=code, minimum=-(1 << 31),
            maximum=(1 << 31) - 1,
        )
        values = {
            key: _bounded_int(
                payload[key], key, code=code, minimum=0,
                maximum=relay.MAX_SEQUENCE,
            )
            for key in ("sequence", "value0", "value1", "value2", "context_seq")
        }
        provenance = _bounded_int(
            payload["provenance"], "provenance", code=code, minimum=0,
            maximum=NEXUS_PROVENANCE_ALL,
        )
        if status == 0 and provenance != NEXUS_SUCCESS_PROVENANCE[tool]:
            raise relay.WireProtocolError(
                code, "successful Nexus tool provenance does not match its contract"
            )
        if status != 0 and provenance != 0:
            raise relay.WireProtocolError(
                code, "failed Nexus tool must not claim successful provenance"
            )
        result = _safe_text(
            payload["result"], "result", code=code, maximum=95, empty=True
        )
        model_projection = payload["model_projection"]
        if not isinstance(model_projection, str) or "\0" in model_projection:
            raise relay.WireProtocolError(
                code, "Nexus tool model projection must be UTF-8 text"
            )
        try:
            model_projection_bytes = model_projection.encode("utf-8")
        except UnicodeEncodeError as error:
            raise relay.WireProtocolError(
                code, "Nexus tool model projection is not UTF-8"
            ) from error
        if len(model_projection_bytes) > NEXUS_WORKSPACE_RESULT_MAX_BYTES:
            raise relay.WireProtocolError(
                code, "Nexus tool model projection is outside bounds"
            )
        projection_value = payload["projection_sha256"]
        if projection_value == "":
            projection_sha256 = ""
        else:
            projection_sha256 = _digest(
                projection_value, "projection digest", code=code
            )
        if status != 0 and projection_sha256:
            raise relay.WireProtocolError(
                code, "failed Nexus tool must not claim a model projection"
            )
        if (status == 0) != bool(model_projection):
            raise relay.WireProtocolError(
                code, "Nexus tool model projection does not match its status"
            )
        data_trust = _safe_text(
            payload["data_trust"],
            "data_trust",
            code=code,
            maximum=32,
            empty=True,
        )
        artifact_value = payload["artifact_sha256"]
        if artifact_value == "":
            artifact_sha256 = ""
        else:
            artifact_sha256 = _digest(
                artifact_value, "artifact digest", code=code
            )
        workspace_source_value = payload["workspace_source_sha256"]
        if workspace_source_value == "":
            workspace_source_sha256 = ""
        else:
            workspace_source_sha256 = _digest(
                workspace_source_value, "workspace source digest", code=code
            )
        if status != 0 and artifact_sha256:
            raise relay.WireProtocolError(
                code, "failed Nexus tool must not claim a successful artifact"
            )
        if status != 0 and data_trust != (
            "untrusted" if tool in NEXUS_WORKSPACE_TOOLS else "none"
        ):
            raise relay.WireProtocolError(
                code, "failed Nexus tool trust label is malformed"
            )
        if status != 0 and workspace_source_sha256:
            raise relay.WireProtocolError(
                code, "failed Nexus tool must not claim workspace source"
            )
        result_sha256 = _digest(
            payload["result_sha256"], "result digest", code=code
        )
        guest_wrapper: dict[str, object] = {
            "status": status,
            "value0": values["value0"],
            "value1": values["value1"],
            "value2": values["value2"],
            "result": result,
        }
        arguments = entry.get("arguments")
        if status == 0:
            if not projection_sha256 or not isinstance(arguments, Mapping):
                raise relay.WireProtocolError(
                    code, "successful Nexus tool lacks its result binding"
                )
            if tool in NEXUS_WORKSPACE_TOOLS:
                expected_projection = entry.get("workspace_result")
                aggregate = entry.get("workspace_aggregate")
                search_complete = True
                if tool == "search_files":
                    search_complete = bool(
                        isinstance(aggregate, Mapping)
                        and not entry.get("workspace_pending_candidates")
                        and (
                            aggregate.get("manifest_eof")
                            or len(aggregate.get("matches", ()))
                            >= workspace.MAX_RESULTS
                        )
                    )
                if (
                    not isinstance(expected_projection, str)
                    or not search_complete
                    or model_projection != expected_projection
                    or entry.get("workspace_content_sha256")
                    != projection_sha256
                    or entry.get("workspace_task_id") != values["value1"]
                    or values["value0"]
                    != len(expected_projection.encode("utf-8"))
                    or result != NEXUS_WORKSPACE_OBSERVATION_RESULT
                    or data_trust != "untrusted"
                    or artifact_sha256 != projection_sha256
                    or workspace_source_sha256
                    != entry.get("workspace_source_sha256")
                ):
                    raise relay.WireProtocolError(
                        code, "workspace observation is not bound to Host bytes"
                    )
            else:
                if workspace_source_sha256:
                    raise relay.WireProtocolError(
                        code, "system observation claimed workspace source"
                    )
                expected_values = _inspect_system_projection_values(
                    arguments.get("operation"), model_projection
                )
                if (
                    (values["value0"], values["value1"]) != expected_values
                    or values["value2"] != 0
                    or result != NEXUS_SYSTEM_OBSERVATION_RESULT
                    or data_trust != "kernel_fact"
                    or artifact_sha256 != projection_sha256
                ):
                    raise relay.WireProtocolError(
                        code, "system observation is not bound to its Task artifact"
                    )
                expected_projection = model_projection
            guest_wrapper["model_projection"] = model_projection
            if not hmac.compare_digest(
                projection_sha256,
                hashlib.sha256(expected_projection.encode("utf-8")).hexdigest(),
            ):
                raise relay.WireProtocolError(
                    code, "Nexus tool projection does not match its exact Guest data"
                )
        expected_result_sha256 = hashlib.sha256(
            relay.canonical_json_bytes(guest_wrapper)
        ).hexdigest()
        if not hmac.compare_digest(result_sha256, expected_result_sha256):
            raise relay.WireProtocolError(
                code, "Nexus tool result digest does not match its exact Guest wrapper"
            )
        assert self._nexus_task_ledger is not None
        try:
            self._nexus_task_ledger.settle_tool(
                corr_id,
                tool=tool,
                status=status,
                value0=values["value0"],
                value1=values["value1"],
                value2=values["value2"],
                provenance=provenance,
                projection_sha256=projection_sha256,
                workspace_source_sha256=workspace_source_sha256,
                context_seq=values["context_seq"],
                result_sha256=result_sha256,
                session_blocked_marker=(
                    result
                    if result == "artifact_cleanup_failed;session_blocked=1"
                    else ""
                ),
            )
            if (
                entry.get("round_limit_pending")
                and not self._nexus_task_ledger.snapshot().termination_cause
            ):
                self._nexus_task_ledger.begin_termination(corr_id, "round_limit")
        except nexus_task_ledger.NexusTaskLedgerError as error:
            raise _nexus_ledger_wire_error(error) from None
        entry.update(
            {
                "settled": True,
                "status": status,
                "sequence": values["sequence"],
                "value0": values["value0"],
                "value1": values["value1"],
                "value2": values["value2"],
                "result": result,
                "context_seq": values["context_seq"],
                "provenance": provenance,
                "projection_sha256": projection_sha256,
                "result_sha256": result_sha256,
                "data_trust": data_trust,
                "artifact_sha256": artifact_sha256,
                "workspace_source_sha256": workspace_source_sha256,
                "model_projection": model_projection,
            }
        )
        event = {
            "type": "tool_event",
            "turn_id": turn.turn_id,
            "request_id": turn.request_id,
            "corr_id": corr_id,
            "tool": tool,
            "status": status,
            **values,
            "result": result,
            "model_projection": model_projection,
            "provenance": provenance,
            "projection_sha256": projection_sha256,
            "result_sha256": result_sha256,
            "data_trust": data_trust,
            "artifact_sha256": artifact_sha256,
            "workspace_source_sha256": workspace_source_sha256,
        }
        self._controller(event)
        telemetry = {
            key: value
            for key, value in event.items()
            if key not in ("result", "model_projection")
        }
        telemetry["event"] = "tool_event"
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
            value["control_id"] = _positive_full_u64(
                control_id, "control_id", code="BAD_TASK_EVENT"
            )
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
        assert self._nexus_task_ledger is not None
        try:
            if self._nexus_user_cancel_root_needs_synthetic_tool_settlement(value):
                self._nexus_task_ledger.settle_cancelled_tool_from_task(
                    value["corr_id"]
                )
                self._nexus_tool_ledger[int(value["corr_id"])]["settled"] = True
            self._nexus_task_ledger.record_event(value)
            if (
                self._nexus_cancel_pending
                and event == "assigned"
                and value["parent_task_id"] == 0
            ):
                self._active_last_corr = int(value["corr_id"])
                self._nexus_task_ledger.begin_cancel(value["corr_id"])
                self._nexus_cancel_pending = False
        except nexus_task_ledger.NexusTaskLedgerError as error:
            raise _nexus_ledger_wire_error(error) from None
        self._controller({"type": "task_event", **value})
        self._telemetry(value, source="guest")

    def _nexus_user_cancel_root_needs_synthetic_tool_settlement(
        self, value: Mapping[str, object]
    ) -> bool:
        """Close only the no-TOOL trace proved by a user-cancel root terminal."""

        if (
            self.active is None
            or not self.active.cancelled
            or value.get("parent_task_id") != 0
            or value.get("event") != "cancelled"
            or value.get("corr_id") != self._active_last_corr
            or self._nexus_task_ledger is None
        ):
            return False
        corr_id = int(value["corr_id"])
        entry = self._nexus_tool_ledger.get(corr_id)
        snapshot = self._nexus_task_ledger.snapshot()
        root_tasks = [task for task in snapshot.tasks if task.parent_task_id == 0]
        if (
            entry is None
            or bool(entry.get("settled"))
            or not snapshot.cancelling
            or snapshot.termination_cause != "user_interrupt"
            or snapshot.latest_corr_id != corr_id
            or snapshot.delivered_tool_count != snapshot.settled_tool_count + 1
            or len(root_tasks) != 1
            or root_tasks[0].task_id != value.get("task_id")
            or root_tasks[0].state != "running"
            or root_tasks[0].event_count != 3
            or root_tasks[0].terminal_event
        ):
            return False
        root = root_tasks[0]
        if (
            value.get("workflow_lifecycle_id") != snapshot.workflow_lifecycle_id
            or value.get("workflow_lifecycle_generation")
            != snapshot.workflow_lifecycle_generation
            or value.get("task_state") != value.get("event")
            or value.get("role") != root.role
            or value.get("agent_pid") != root.agent_pid
            or value.get("agent_id") != root.agent_id
            or value.get("control_id_known") != root.control_id_known
            or value.get("control_id", 0) != root.control_id
            or value.get("source_pid") != root.agent_pid
            or value.get("target_pid") != root.agent_pid
            or value.get("deadline_tick", 0) != 0
            or value.get("artifact_handle", 0) != 0
            or value.get("metric_code", 0) != 0
            or value.get("metric_value", 0) != 0
            or value.get("context_seq", 0) != 0
            or value.get("provenance", 0) != 0
            or value.get("resource_used", 0) != 0
            or value.get("digest", "") != ""
            or value.get("artifact_sha256", "") != ""
        ):
            return False
        cancelled_children = [
            task
            for task in snapshot.tasks
            if task.parent_task_id != 0
            and task.assigned_corr_id == corr_id
            and task.terminal_event == "cancelled"
            and task.terminal_status == nexus_task_ledger.AGENT_STATUS_CANCELLED
        ]
        if len(cancelled_children) != 1:
            return False
        return (
            value.get("status") == nexus_task_ledger.AGENT_STATUS_CANCELLED
            and value.get("summary") == "turn_cancelled"
        )

    def _turn_complete(self, payload: dict[str, object]) -> None:
        turn = self._match_active(payload)
        status = _text(payload.get("status", "completed"), "turn status", maximum=32)
        if status not in ("completed", "cancelled", "error"):
            raise relay.WireProtocolError("BAD_TURN", "unsupported turn completion status")
        terminal_context_sequence = 0
        if self.guest_profile == "nexus":
            if status == "completed":
                terminal_context_sequence = _positive_full_u64(
                    payload.get("context_seq"), "context_seq", code="BAD_TURN"
                )
            else:
                terminal_context_sequence = _full_u64(
                    payload.get("context_seq"), "context_seq", code="BAD_TURN"
                )
                expected_rollback_sequence = (
                    turn.context_start_sequence
                    if turn.context_start_known
                    else (
                        self._nexus_context_head_sequence
                        if self._nexus_context_head_known
                        else None
                    )
                )
                if (
                    expected_rollback_sequence is not None
                    and terminal_context_sequence != expected_rollback_sequence
                ):
                    raise relay.WireProtocolError(
                        "BAD_TURN",
                        "rollback context_seq does not match the pinned turn-start head",
                    )
        if "answer" in payload and "content" in payload:
            raise relay.WireProtocolError(
                "BAD_TURN", "turn completion contains ambiguous answer fields"
            )
        answer = payload.get("answer", payload.get("content", ""))
        if answer != "":
            answer = _text(
                answer, "turn answer", maximum=self.max_final_bytes
            )
        if (
            self.guest_profile == "nexus"
            and status in ("cancelled", "error")
            and answer != ""
        ):
            raise relay.WireProtocolError(
                "BAD_TURN",
                "cancelled or failed Nexus turns must not contain an answer",
            )
        if self.guest_profile == "nexus" and status == "completed":
            if terminal_context_sequence <= turn.context_visible_head_sequence:
                raise relay.WireProtocolError(
                    "BAD_TURN",
                    "Nexus final Context node must follow every observed visible head",
                )
            delivered = self._last_final_response
            if (
                delivered is None
                or delivered[0] != self._last_model_response_corr
                or not isinstance(answer, str)
                or not hmac.compare_digest(
                    answer.encode("utf-8"), delivered[1].encode("utf-8")
                )
            ):
                raise relay.WireProtocolError(
                    "FINAL_MISMATCH",
                    "Nexus turn answer does not match the final delivered model response",
                )
            if (
                not self._nexus_final_frozen
                or any(
                    not bool(entry.get("settled"))
                    for entry in self._nexus_tool_ledger.values()
                )
            ):
                raise relay.WireProtocolError(
                    "BAD_TURN", "Nexus final proof ledger is incomplete"
                )
        if self.guest_profile == "nexus":
            expected_counts = {
                "rounds": turn.rounds,
                "retries": turn.retries,
                "attempts": turn.attempts,
            }
            if set(expected_counts).difference(payload):
                raise relay.WireProtocolError(
                    "BAD_TURN",
                    "Nexus turn completion lacks its negotiated budget counters",
                )
            for field, expected in expected_counts.items():
                observed = payload[field]
                if (
                    not isinstance(observed, int)
                    or isinstance(observed, bool)
                    or observed != expected
                ):
                    raise relay.WireProtocolError(
                        "BAD_TURN",
                        "Nexus turn completion counters do not match the Host",
                    )
        task_snapshot: nexus_task_ledger.NexusTaskLedgerSnapshot | None = None
        if self._nexus_task_ledger is not None:
            try:
                if status == "error" and turn.cancelled:
                    corr_id = self._nexus_task_ledger.cancelled_cleanup_pending_corr
                    if corr_id:
                        entry = self._nexus_tool_ledger.get(corr_id)
                        if entry is None or bool(entry.get("settled")):
                            raise relay.WireProtocolError(
                                "BAD_TURN",
                                "cancel-derived cleanup Host ledger is inconsistent",
                            )
                        if not self._nexus_task_ledger.settle_cancelled_cleanup_tool_at_turn_complete(
                            corr_id, turn_status=status
                        ):
                            raise relay.WireProtocolError(
                                "BAD_TURN",
                                "cancel-derived cleanup candidate disappeared",
                            )
                        entry["settled"] = True
                task_snapshot = self._nexus_task_ledger.assert_turn_complete(status)
            except nexus_task_ledger.NexusTaskLedgerError as error:
                if (
                    error.reason == "required Task/tool/identity proof is incomplete"
                    and self._task_proof_waits_only_for_identity()
                ):
                    self._pending_turn_complete = (
                        dict(payload),
                        status,
                        time.monotonic() + NEXUS_TURN_PROOF_GRACE_SECONDS,
                    )
                    return
                raise _nexus_ledger_wire_error(error) from None
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
        if self.guest_profile == "nexus":
            assert task_snapshot is not None
            root_tasks = [task for task in task_snapshot.tasks if task.parent_task_id == 0]
            if len(root_tasks) != 1:
                raise relay.WireProtocolError(
                    "BAD_TURN", "Nexus task proof has no unique root"
                )
            final_corr_id = (
                self._last_final_corr_id
                if status == "completed"
                else root_tasks[0].terminal_corr_id
            )
            final_values = {
                "version": 1,
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "final_corr_id": final_corr_id,
                "final_request_sha256": self._last_final_request_sha256,
                "final_response_sha256": self._last_final_response_sha256,
                "provider_proof_sha256": self._last_final_provider_proof_sha256,
                "final_task_root": task_snapshot.task_root_sha256,
                "final_artifact_root": task_snapshot.artifact_root_sha256,
            }
            if tuple(final_values) != NEXUS_FINAL_PROOF_FIELDS:
                raise AssertionError("Nexus final proof fields changed")
            event.update(final_values)
            event["final_proof_root"] = hashlib.sha256(
                relay.canonical_json_bytes(final_values)
            ).hexdigest()
            event.update(
                {
                    "rounds": turn.rounds,
                    "retries": turn.retries,
                    "attempts": turn.attempts,
                }
            )
            if not turn.context_start_known and status != "completed":
                turn.context_start_sequence = terminal_context_sequence
                turn.context_start_known = True
            self._nexus_context_head_sequence = terminal_context_sequence
            self._nexus_context_head_known = True
            if status == "completed":
                pair_sha256 = hashlib.sha256(
                    turn.user_content.encode("utf-8")
                    + b"\0"
                    + str(answer).encode("utf-8")
                ).hexdigest()
                self._nexus_completed_turn_bindings.append(
                    (
                        turn.turn_id,
                        turn.request_id,
                        turn.context_user_sequence,
                        terminal_context_sequence,
                        pair_sha256,
                    )
                )
                del self._nexus_completed_turn_bindings[
                    :-nexus_contract.CONTEXT_PATH_MAX_TURNS
                ]
        for optional in (("rounds",) if self.guest_profile != "nexus" else ()) + (
            "context_seq",
        ):
            if optional in payload:
                event[optional] = payload[optional]
        self.active = None
        self._last_model_response_corr = 0
        self._last_final_response = None
        self._nexus_tool_ledger.clear()
        self._last_final_request_sha256 = ""
        self._nexus_final_frozen = False
        self._nexus_cancel_pending = False
        self._active_last_corr = 0
        self._last_final_response_sha256 = ""
        self._last_final_provider_proof_sha256 = ""
        self._last_final_corr_id = 0
        self._pending_turn_complete = None
        self._approval_bindings.clear()
        if self._nexus_task_ledger is not None:
            self._nexus_task_ledger.clear()
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
            self._nexus_completed_turn_bindings.clear()
            self._nexus_context_head_sequence = 0
            self._nexus_context_head_known = True
            self._nexus_tool_ledger.clear()
            self._last_final_request_sha256 = ""
            self._nexus_final_frozen = False
            self._nexus_cancel_pending = False
            self._active_last_corr = 0
            self._last_final_response_sha256 = ""
            self._last_final_provider_proof_sha256 = ""
            self._last_final_corr_id = 0
            if self._nexus_task_ledger is not None:
                self._nexus_task_ledger.clear()
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
        if (
            self._nexus_task_ledger is not None
            and role in nexus_task_ledger.BUSINESS_ROLES
        ):
            try:
                self._nexus_task_ledger.set_kernel_identity(
                    role=role,
                    pid=pid,
                    agent_id=agent_id,
                    control_id=actor_control_id,
                )
            except nexus_task_ledger.NexusTaskLedgerError as error:
                raise relay.WireProtocolError("BAD_TELEMETRY", error.reason) from None

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
                payload.get("actor_control_id"), "actor_control_id", minimum=1,
                maximum=NEXUS_FULL_U64_MAX,
            )
            role = _text(payload.get("role"), "kernel audit role", maximum=24)
            if role not in TASK_ROLES:
                raise relay.WireProtocolError(
                    "BAD_TELEMETRY", "kernel audit role is unsupported"
                )
            self._telemetry_int(
                payload.get("audit_kind"), "audit_kind", minimum=0, maximum=0xFFFFFFFF
            )
            self._telemetry_int(
                payload.get("loop_state"), "loop_state", minimum=0, maximum=0xFFFFFFFF
            )
            self._telemetry_int(
                payload.get("tool_id"), "tool_id", minimum=0, maximum=0xFFFFFFFF
            )
            self._telemetry_int(
                payload.get("event_type"), "event_type", minimum=0, maximum=0xFFFFFFFF
            )
            self._telemetry_int(
                payload.get("source_pid"),
                "source_pid",
                minimum=1,
                maximum=(1 << 31) - 1,
            )
            self._telemetry_int(
                payload.get("target_pid"),
                "target_pid",
                minimum=1,
                maximum=(1 << 31) - 1,
            )
            self._telemetry_int(
                payload.get("status"),
                "status",
                minimum=-(1 << 31),
                maximum=(1 << 31) - 1,
            )
            self._telemetry_int(payload.get("value0"), "value0", minimum=0)
            self._telemetry_int(payload.get("value1"), "value1", minimum=0)
            self._telemetry_int(payload.get("value2"), "value2", minimum=0)
            self._telemetry_int(
                payload.get("provenance"), "provenance", minimum=0
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
            payload.get("actor_control_id"), "actor_control_id", minimum=1,
            maximum=NEXUS_FULL_U64_MAX,
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
            self._retry_pending_turn_complete()
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
        blocked_guest_close = bool(
            self.guest_profile == "nexus"
            and self.active is None
            and self._nexus_task_ledger is not None
            and self._nexus_task_ledger.snapshot().session_blocked
        )
        if not self.closing and not blocked_guest_close:
            raise relay.WireProtocolError("BAD_CLOSE", "Guest closed an active session")
        reason = _text(payload.get("reason", "guest_complete"), "close reason", maximum=64)
        if blocked_guest_close:
            reason = "session_error"
        if isinstance(self.provider, relay.ReplayProvider):
            self.provider.assert_exhausted()
        if self._nexus_task_ledger is not None:
            self._nexus_task_ledger.clear(reset_session=True)
        self._nexus_completed_turn_bindings.clear()
        self._nexus_context_head_sequence = 0
        self._nexus_context_head_known = False
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
            if key == "context_path":
                projected = _context_path_metadata(item)
                if projected is not None:
                    value[key] = projected
                continue
            if key == "history_bindings":
                if (
                    isinstance(item, list)
                    and len(item) <= NEXUS_MAX_HISTORY_PROJECTIONS
                    and all(
                        isinstance(binding, dict)
                        and set(binding)
                        == {
                            "tool_corr_id",
                            "tool",
                            "projection_sha256",
                            "projection_field",
                            "data_trust",
                        }
                        and isinstance(binding["tool_corr_id"], int)
                        and not isinstance(binding["tool_corr_id"], bool)
                        and 0 < binding["tool_corr_id"] <= relay.MAX_SEQUENCE
                        and isinstance(binding["tool"], str)
                        and relay.TOOL_NAME_RE.fullmatch(binding["tool"])
                        is not None
                        and isinstance(binding["projection_sha256"], str)
                        and relay.WIRE_DIGEST_RE.fullmatch(
                            binding["projection_sha256"]
                        )
                        is not None
                        and binding["projection_field"]
                        in ("model_projection", "runtime_observation")
                        and binding["data_trust"]
                        in ("", "guest_runtime_untrusted")
                        for binding in item
                    )
                ):
                    value[key] = [dict(binding) for binding in item]
                continue
            if key == "forced_tool" and item is None:
                value[key] = None
                continue
            if key in FULL_U64_CONTROL_FIELDS:
                if (
                    isinstance(item, int)
                    and not isinstance(item, bool)
                    and 0 <= item <= NEXUS_FULL_U64_MAX
                ):
                    value[key] = item
                continue
            if isinstance(item, bool):
                value[key] = item
            elif isinstance(item, int):
                if -(1 << 63) <= item <= relay.MAX_SEQUENCE:
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
        # Admission calls _Peer.send() while holding this lock so that welcome
        # and the role-specific snapshot are an indivisible stream prefix.
        # A failed send closes the peer and synchronously calls _remove(), so
        # this must be re-entrant.
        self._lock = threading.RLock()
        self._controller: _Peer | None = None
        self._observers: set[_Peer] = set()
        self._kernel_identity_prefix_by_pid: dict[int, dict[str, object]] = {}
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
            self._cache_kernel_identity(message)
            peers = tuple(self._observers)
        for peer in peers:
            # A full per-observer queue disconnects only that observer.  It can
            # never block serial/model progress or faster observers.
            peer.send(message)

    def _cache_kernel_identity(self, message: Mapping[str, object]) -> None:
        """Keep the latest validated kernel identity envelope unchanged per PID."""

        source = message.get("source")
        if source == "kernel_audit":
            required = KERNEL_AUDIT_REQUIRED_FIELDS
        elif source == "kernel_snapshot":
            required = KERNEL_SNAPSHOT_REQUIRED_FIELDS
        else:
            return
        pid = message.get("pid")
        if (
            message.get("type") != "telemetry"
            or not required.issubset(message)
            or message.get("event") != source
            or message.get("fresh") is not (source == "kernel_audit")
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or not 1 <= pid <= (1 << 31) - 1
            or message.get("role") not in TASK_ROLES
        ):
            return
        cache = getattr(self, "_kernel_identity_prefix_by_pid", None)
        if cache is None:
            # Some focused transport tests construct a deliberately bare
            # endpoint without invoking __init__.
            cache = {}
            self._kernel_identity_prefix_by_pid = cache
        if pid in cache:
            del cache[pid]
        elif len(cache) >= MAX_KERNEL_IDENTITY_REPLAY_PIDS:
            del cache[next(iter(cache))]
        cache[pid] = copy.deepcopy(dict(message))

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
        with self._lock:
            # Serialize the stop linearization point with admission's final
            # check and publication.  Whichever holds the lock first wins;
            # once stopping is visible, no later peer can be published.
            self.stopping.set()
            peers = tuple(self._observers) + (
                (self._controller,) if self._controller else ()
            )
            self._controller = None
            self._observers.clear()
        for server in (self.control_server, self.telemetry_server):
            try:
                server.close()
            except OSError:
                pass
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

    def _admit_peer(self, peer: _Peer) -> tuple[bool, str | None]:
        """Stage an authenticated peer's complete prefix before publishing it."""

        with self._lock:
            if self.stopping.is_set():
                peer.close()
                return False, None

            if peer.role == "controller":
                if self._controller is not None and not self._controller.closed.is_set():
                    return False, "CONTROLLER_BUSY"
                initial = self.controller_initial
            elif peer.role == "observer":
                # Closed peers normally remove themselves synchronously.  The
                # filter also keeps a delayed callback from consuming capacity.
                self._observers = {
                    current
                    for current in self._observers
                    if not current.closed.is_set()
                }
                if len(self._observers) >= MAX_LOCAL_CLIENTS:
                    return False, "OBSERVER_LIMIT"
                initial = self.observer_initial
            else:
                peer.close()
                return False, None

            if not peer.send({"type": "welcome", "role": peer.role}):
                return False, None
            if initial is not None:
                try:
                    snapshot = initial()
                except Exception:
                    peer.close()
                    return False, None
                if not peer.send(snapshot):
                    return False, None
            if peer.role == "observer":
                cached_prefix = getattr(
                    self, "_kernel_identity_prefix_by_pid", {}
                )
                for cached in tuple(cached_prefix.values()):
                    if not peer.send(cached):
                        return False, None

            # Check again after all potentially fallible prefix work and
            # immediately before making the peer visible to fan-out.
            if self.stopping.is_set() or peer.closed.is_set():
                peer.close()
                return False, None

            if peer.role == "controller":
                self._controller = peer
            else:
                self._observers.add(peer)
            return True, None

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
                admitted, rejection = self._admit_peer(peer)
                if rejection is not None:
                    try:
                        connection.sendall(
                            local.encode_message({"type": "error", "code": rejection})
                        )
                    finally:
                        peer.close()
                    continue
                if not admitted:
                    peer.close()
                    continue
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
        max_payload: int | None,
        max_rounds: int | None,
        max_tokens: int | None,
        boot_timeout: float,
        quiet: bool = False,
        shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
        guest_profile: str = "agentlive",
        workspace_reader: workspace.WorkspaceReader | None = None,
    ) -> None:
        if not 0 < shutdown_grace_seconds <= 30:
            raise ValueError("shutdown grace must be in (0, 30] seconds")
        if (
            not isinstance(guest_profile, str)
            or guest_profile not in local.GUEST_PROFILES
        ):
            raise ValueError("Guest profile is unsupported")
        if guest_profile != "nexus" and workspace_reader is not None:
            raise ValueError("workspace access is only valid for the Nexus profile")
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
            provider_name=provider_name,
            model_name=model_name,
            workspace_reader=workspace_reader,
        )
        self.endpoints.controller_initial = lambda: {
            "type": "session_ready",
            "session_id": self.session.session_id,
            "max_rounds": self.session.max_rounds,
            **(
                {"max_retries": self.session.max_retries}
                if self.guest_profile == "nexus"
                else {}
            ),
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
                self.session.poll_turn_proof()
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
            binding_fields = APPROVAL_BINDING_FIELDS
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


def _configured_workspace_reader(
    args: argparse.Namespace,
) -> workspace.WorkspaceReader | None:
    root = args.workspace_root
    if args.guest_profile == "nexus":
        if root is None:
            raise ValueError("--workspace-root is required for --guest-profile nexus")
        return workspace.WorkspaceReader(root)
    if root is not None:
        raise ValueError("--workspace-root requires --guest-profile nexus")
    return None


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
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--max-payload-bytes", type=int, default=None)
    parser.add_argument("--http-timeout", type=float, default=relay.DEFAULT_HTTP_TIMEOUT_SECONDS)
    parser.add_argument("--max-http-response-bytes", type=int, default=relay.DEFAULT_MAX_HTTP_RESPONSE_BYTES)
    parser.add_argument("--boot-timeout", type=float, default=DEFAULT_BOOT_TIMEOUT_SECONDS)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--workspace-root", type=Path)
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
    return relay._build_provider(
        args,
        serialize_auto_tool_calls=args.guest_profile == "nexus",
    )


def main(argv: Sequence[str] | None = None) -> int:
    daemon: InteractiveRelayDaemon | None = None
    try:
        args = _parser().parse_args(argv)
        workspace_reader = _configured_workspace_reader(args)
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
            workspace_reader=workspace_reader,
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
