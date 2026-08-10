#!/usr/bin/env python3
"""Pure user-space MCP 2026-07-28 and A2A v1 Task gateway.

This module intentionally has no HTTP, OAuth, JWS, file fetching, or kernel ABI
dependency.  Binding-specific code authenticates a request and constructs an
envelope; this module then maps validated protocol objects onto the narrow Task
Channel transport.  A future binary adapter only has to translate the transport
dataclasses to SQ/CQ records and typed resource handles.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

if __package__:
    from .agent_task_transport import (
        TERMINAL_TASK_STATUSES,
        InvalidTaskTransitionError,
        TaskBinding,
        TaskChannelEvent,
        TaskChannelIssuer,
        TaskChannelRequest,
        TaskChannelSnapshot,
        TaskChannelTransport,
        TaskEventKind,
        TaskStatus,
    )
else:
    from agent_task_transport import (  # type: ignore[no-redef]
        TERMINAL_TASK_STATUSES,
        InvalidTaskTransitionError,
        TaskBinding,
        TaskChannelEvent,
        TaskChannelIssuer,
        TaskChannelRequest,
        TaskChannelSnapshot,
        TaskChannelTransport,
        TaskEventKind,
        TaskStatus,
    )


MCP_PROTOCOL_VERSION = "2026-07-28"
A2A_PROTOCOL_VERSION = "1.0"
MCP_TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
AGENTOS_TASK_METADATA = "io.agentos/task-channel"

_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9._~-]{1,512}\Z")
_JSONRPC_ID_LIMIT = (1 << 53) - 1


class ProtocolGatewayError(RuntimeError):
    """A fail-closed protocol error with stable public classification."""

    def __init__(self, code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.public_message = message


class TaskNotFoundError(ProtocolGatewayError):
    def __init__(self) -> None:
        super().__init__(-32001, "TASK_NOT_FOUND", "Task not found")


class UnsupportedProtocolVersionError(ProtocolGatewayError):
    def __init__(self, protocol: str, version: str) -> None:
        super().__init__(
            -32022,
            "UNSUPPORTED_PROTOCOL_VERSION",
            f"unsupported {protocol} protocol version {version!r}",
        )


class UnauthorizedGatewayError(ProtocolGatewayError):
    def __init__(self, message: str = "request identity is not authorized") -> None:
        super().__init__(-32030, "UNAUTHORIZED", message)


class InvalidProtocolRequestError(ProtocolGatewayError):
    def __init__(self, message: str, *, code: int = -32602, reason: str = "INVALID_ARGUMENT") -> None:
        super().__init__(code, reason, message)


@dataclass(frozen=True)
class GatewayPrincipal:
    """Identity already authenticated by the binding-specific user-space layer."""

    issuer: str
    tenant: str
    subject: str

    def __post_init__(self) -> None:
        for name in ("issuer", "tenant", "subject"):
            value = getattr(self, name)
            if not value or len(value) > 256:
                raise ValueError(f"{name} must be a non-empty bounded string")

    @property
    def owner_key(self) -> tuple[str, str, str]:
        return (self.issuer, self.tenant, self.subject)


IdentityValidator = Callable[[GatewayPrincipal, str, str], None]
SchemaValidator = Callable[[Mapping[str, object], object], None]
A2AToolRouter = Callable[[Mapping[str, object]], str]


class StaticIssuerPolicy:
    """Small explicit policy useful for local gateways and deterministic tests."""

    def __init__(self, issuers: Mapping[str, frozenset[str] | None]) -> None:
        self._issuers = dict(issuers)

    def __call__(self, principal: GatewayPrincipal, protocol: str, operation: str) -> None:
        del protocol, operation
        tenants = self._issuers.get(principal.issuer)
        if principal.issuer not in self._issuers:
            raise UnauthorizedGatewayError("credential issuer is not trusted")
        if tenants is not None and principal.tenant not in tenants:
            raise UnauthorizedGatewayError("credential issuer is not trusted for this tenant")


@dataclass(frozen=True)
class McpRequestEnvelope:
    """HTTP-neutral routing facts validated before any Task Channel submission."""

    body: Mapping[str, object]
    protocol_version: str
    method_header: str
    name_header: str | None = None
    origin: str | None = None


@dataclass(frozen=True)
class A2ARequestEnvelope:
    body: Mapping[str, object]
    protocol_version: str
    operation: str
    tenant: str


def _validate_json_tree(value: object, *, path: str = "$") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_tree(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} contains a non-string object key")
            _validate_json_tree(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value")


def canonical_json_bytes(value: object) -> bytes:
    """Return stable user-space JSON bytes for remote schema discovery."""

    _validate_json_tree(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("value cannot be canonically encoded as JSON") from exc


def canonical_schema_digest(schema: Mapping[str, object]) -> str:
    if not isinstance(schema, Mapping):
        raise ValueError("tool schema must be a JSON object")
    return hashlib.sha256(canonical_json_bytes(schema)).hexdigest()


@dataclass(frozen=True)
class ToolManifest:
    """Frozen user-space projection of one execution-contract tool node."""

    name: str
    tool_id: int
    contract_node_id: int
    input_schema: Mapping[str, object]
    kernel_manifest_digest: str
    description: str = ""
    output_schema: Mapping[str, object] | None = None
    task_mode: str = "sync"
    deadline_ns: int | None = None
    ttl_ms: int | None = 3_600_000
    poll_interval_ms: int = 250

    def __post_init__(self) -> None:
        if _TOOL_NAME_RE.fullmatch(self.name) is None:
            raise ValueError("tool name is not canonical")
        if type(self.tool_id) is not int or self.tool_id <= 0 or self.tool_id > 0xFFFF:
            raise ValueError("tool_id must fit the kernel Task Channel uint16 field")
        if (
            type(self.contract_node_id) is not int
            or self.contract_node_id < 0
            or self.contract_node_id > 0xFFFFFFFF
        ):
            raise ValueError("contract_node_id must be a uint32")
        if self.task_mode not in ("sync", "task"):
            raise ValueError("task_mode must be 'sync' or 'task'")
        if self.deadline_ns is not None and (
            type(self.deadline_ns) is not int or self.deadline_ns <= 0
        ):
            raise ValueError("deadline_ns must be positive when present")
        if self.ttl_ms is not None and (type(self.ttl_ms) is not int or self.ttl_ms <= 0):
            raise ValueError("ttl_ms must be positive or None")
        if type(self.poll_interval_ms) is not int or self.poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.kernel_manifest_digest) is None:
            raise ValueError("kernel_manifest_digest must be an authoritative lowercase SHA-256")
        canonical_schema_digest(self.input_schema)
        if self.output_schema is not None:
            canonical_schema_digest(self.output_schema)

    @property
    def remote_schema_digest(self) -> str:
        return canonical_schema_digest(self.input_schema)


@dataclass(frozen=True)
class StoredTask:
    task_id: str
    owner_key: tuple[str, str, str]
    binding: TaskBinding
    tool_name: str
    protocol: str
    context_id: str
    created_at: str
    last_updated_at: str
    ttl_ms: int | None
    poll_interval_ms: int
    history: tuple[Mapping[str, object], ...] = ()
    artifacts: tuple[Mapping[str, object], ...] = ()
    last_transport_sequence: int = 0


class TaskStore(Protocol):
    def create(self, task: StoredTask) -> None:
        ...

    def get(self, task_id: str) -> StoredTask | None:
        ...

    def replace(self, task: StoredTask) -> None:
        ...

    def list_owner(self, owner_key: tuple[str, str, str]) -> tuple[StoredTask, ...]:
        ...


class InMemoryTaskStore:
    """Deterministic persistent mapping for one gateway process lifetime."""

    def __init__(self) -> None:
        self._tasks: dict[str, StoredTask] = {}
        self._lock = threading.RLock()

    def create(self, task: StoredTask) -> None:
        with self._lock:
            if task.task_id in self._tasks:
                raise ValueError("task_id already exists")
            self._tasks[task.task_id] = copy.deepcopy(task)

    def get(self, task_id: str) -> StoredTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return None if task is None else copy.deepcopy(task)

    def replace(self, task: StoredTask) -> None:
        with self._lock:
            if task.task_id not in self._tasks:
                raise ValueError("cannot replace an unknown task")
            self._tasks[task.task_id] = copy.deepcopy(task)

    def list_owner(self, owner_key: tuple[str, str, str]) -> tuple[StoredTask, ...]:
        with self._lock:
            return tuple(
                copy.deepcopy(task)
                for task in sorted(self._tasks.values(), key=lambda item: item.task_id)
                if task.owner_key == owner_key
            )


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _iso8601(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("gateway clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _default_schema_validator(schema: Mapping[str, object], value: object) -> None:
    """Validate the closed object subset used by tool manifests in this repo."""

    expected_type = schema.get("type")
    if expected_type not in (None, "object"):
        raise InvalidProtocolRequestError("tool input schema root must be an object")
    if not isinstance(value, Mapping):
        raise InvalidProtocolRequestError("tool arguments must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(type(item) is not str for item in required):
        raise InvalidProtocolRequestError("tool schema has an invalid required list")
    missing = [item for item in required if item not in value]
    if missing:
        raise InvalidProtocolRequestError(f"tool arguments are missing {missing[0]!r}")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise InvalidProtocolRequestError("tool schema properties must be an object")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise InvalidProtocolRequestError(f"tool arguments contain unknown field {unknown[0]!r}")
    type_checks = {
        "object": lambda item: isinstance(item, Mapping),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: type(item) is str,
        "integer": lambda item: type(item) is int,
        "number": lambda item: type(item) in (int, float),
        "boolean": lambda item: type(item) is bool,
        "null": lambda item: item is None,
    }
    for key, item in value.items():
        rule = properties.get(key)
        if not isinstance(rule, Mapping):
            continue
        declared = rule.get("type")
        if type(declared) is str:
            check = type_checks.get(declared)
            if check is None:
                raise InvalidProtocolRequestError(f"tool schema uses unsupported type {declared!r}")
            if not check(item):
                raise InvalidProtocolRequestError(f"tool argument {key!r} has the wrong type")
        if "const" in rule and item != rule["const"]:
            raise InvalidProtocolRequestError(f"tool argument {key!r} violates const")
        enum = rule.get("enum")
        if enum is not None and (not isinstance(enum, list) or item not in enum):
            raise InvalidProtocolRequestError(f"tool argument {key!r} is outside enum")
    _validate_json_tree(value)


class McpA2AGateway:
    """Maps MCP and A2A protocol objects onto one typed Task state machine."""

    def __init__(
        self,
        *,
        transport: TaskChannelTransport,
        channel_issuer: TaskChannelIssuer,
        contract_generation: int,
        tools: tuple[ToolManifest, ...],
        identity_validator: IdentityValidator,
        task_store: TaskStore | None = None,
        schema_validator: SchemaValidator = _default_schema_validator,
        a2a_tool_router: A2AToolRouter | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = _default_clock,
        a2a_tenant: str | None = None,
    ) -> None:
        if not callable(identity_validator):
            raise ValueError("identity_validator is required and must be callable")
        self._transport = transport
        self._channel_issuer = channel_issuer
        if (
            type(contract_generation) is not int
            or contract_generation <= 0
            or contract_generation > (1 << 64) - 1
        ):
            raise ValueError("contract_generation must be a positive uint64")
        self._contract_generation = contract_generation
        self._identity_validator = identity_validator
        self._store = task_store or InMemoryTaskStore()
        self._schema_validator = schema_validator
        self._a2a_router = a2a_tool_router or self._route_a2a_by_skill
        self._id_factory = id_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock
        self._a2a_tenant = a2a_tenant
        self._id_lock = threading.RLock()
        self._context_ids: set[str] = set()

        registry: dict[str, ToolManifest] = {}
        tool_ids: set[int] = set()
        nodes: set[int] = set()
        for tool in tools:
            if tool.name in registry or tool.tool_id in tool_ids or tool.contract_node_id in nodes:
                raise ValueError("tool names, IDs, and contract nodes must be unique")
            # Canonical round-trip makes later caller mutation unable to change a digest.
            input_schema = json.loads(canonical_json_bytes(tool.input_schema).decode("ascii"))
            output_schema = (
                None
                if tool.output_schema is None
                else json.loads(canonical_json_bytes(tool.output_schema).decode("ascii"))
            )
            frozen = replace(tool, input_schema=input_schema, output_schema=output_schema)
            registry[frozen.name] = frozen
            tool_ids.add(frozen.tool_id)
            nodes.add(frozen.contract_node_id)
        if not registry:
            raise ValueError("at least one frozen tool manifest is required")
        self._tools = registry

    def _authorize(self, principal: GatewayPrincipal, protocol: str, operation: str) -> None:
        try:
            self._identity_validator(principal, protocol, operation)
        except ProtocolGatewayError:
            raise
        except Exception as exc:
            raise UnauthorizedGatewayError() from exc

    def _new_id(self, prefix: str, *, task: bool) -> str:
        with self._id_lock:
            for _ in range(32):
                token = self._id_factory()
                if type(token) is not str or not token or _ID_RE.fullmatch(token) is None:
                    raise ValueError("id_factory must return a canonical opaque token")
                value = prefix + token
                if _ID_RE.fullmatch(value) is None:
                    raise ValueError("id_factory token is too long for the selected ID prefix")
                if task:
                    if self._store.get(value) is None:
                        return value
                elif value not in self._context_ids:
                    self._context_ids.add(value)
                    return value
            raise RuntimeError("opaque ID collision budget exhausted")

    def _owned_task(self, principal: GatewayPrincipal, task_id: object) -> StoredTask:
        if type(task_id) is not str or _ID_RE.fullmatch(task_id) is None:
            raise TaskNotFoundError()
        task = self._store.get(task_id)
        if task is None or task.owner_key != principal.owner_key:
            # Unknown and foreign IDs are deliberately indistinguishable.
            raise TaskNotFoundError()
        return task

    @staticmethod
    def _route_a2a_by_skill(message: Mapping[str, object]) -> str:
        metadata = message.get("metadata")
        if not isinstance(metadata, Mapping) or type(metadata.get("skill")) is not str:
            raise InvalidProtocolRequestError("A2A message metadata must select a registered skill")
        return metadata["skill"]  # type: ignore[return-value]

    def _tool(self, name: object) -> ToolManifest:
        if type(name) is not str or name not in self._tools:
            raise InvalidProtocolRequestError("unknown tool", code=-32602, reason="TOOL_NOT_FOUND")
        return self._tools[name]

    def _submit(
        self,
        *,
        tool: ToolManifest,
        payload: object,
        submission_key: str,
        provenance: tuple[str, ...],
        link_request_id: int | None = None,
    ):
        # All authority-bearing fields come from the frozen manifest or local binding.
        request = TaskChannelRequest(
            tool_id=tool.tool_id,
            # This is the digest returned by the kernel manifest query.  It is
            # intentionally distinct from the remote JSON-schema cache digest.
            schema_digest=tool.kernel_manifest_digest,
            contract_node_id=tool.contract_node_id,
            contract_generation=self._contract_generation,
            attempt_id=1,
            payload=copy.deepcopy(payload),
            submission_key=submission_key,
            deadline_ns=tool.deadline_ns,
            link_request_id=link_request_id,
            provenance=provenance,
        )
        return self._transport.submit(self._channel_issuer, request)

    def _create_record(
        self,
        *,
        task_id: str,
        principal: GatewayPrincipal,
        binding: TaskBinding,
        tool: ToolManifest,
        protocol: str,
        context_id: str,
        history: tuple[Mapping[str, object], ...] = (),
    ) -> StoredTask:
        now = _iso8601(self._clock)
        task = StoredTask(
            task_id=task_id,
            owner_key=principal.owner_key,
            binding=binding,
            tool_name=tool.name,
            protocol=protocol,
            context_id=context_id,
            created_at=now,
            last_updated_at=now,
            ttl_ms=tool.ttl_ms,
            poll_interval_ms=tool.poll_interval_ms,
            history=copy.deepcopy(history),
        )
        self._store.create(task)
        return task

    def _sync_record(self, task: StoredTask) -> StoredTask:
        events = self._transport.events(
            self._channel_issuer,
            task.binding,
            after_sequence=task.last_transport_sequence,
        )
        if not events:
            return task
        artifacts = list(task.artifacts)
        for event in events:
            if event.kind is TaskEventKind.ARTIFACT and event.artifact is not None:
                artifact = copy.deepcopy(dict(event.artifact))
                index = event.artifact_index
                if index is None or index < 0 or index > len(artifacts):
                    raise InvalidProtocolRequestError("transport emitted an invalid artifact index")
                if index == len(artifacts):
                    artifacts.append(artifact)
                elif event.append:
                    existing = copy.deepcopy(dict(artifacts[index]))
                    old_parts = existing.get("parts", [])
                    new_parts = artifact.get("parts", [])
                    if not isinstance(old_parts, list) or not isinstance(new_parts, list):
                        raise InvalidProtocolRequestError("artifact parts must be arrays")
                    existing["parts"] = old_parts + new_parts
                    artifacts[index] = existing
                else:
                    artifacts[index] = artifact
        updated = replace(
            task,
            artifacts=tuple(artifacts),
            last_transport_sequence=events[-1].sequence,
            last_updated_at=_iso8601(self._clock),
        )
        self._store.replace(updated)
        return updated

    @staticmethod
    def _task_metadata(snapshot: TaskChannelSnapshot | TaskChannelEvent, binding: TaskBinding) -> dict[str, object]:
        return {
            AGENTOS_TASK_METADATA: {
                "lifecycleId": binding.lifecycle_id,
                "lifecycleGeneration": binding.lifecycle_generation,
                "contractGeneration": binding.contract_generation,
                "channelGeneration": binding.channel_generation,
                "requestId": binding.request_id,
                "contextSequence": snapshot.context_sequence,
                "evidenceTicket": snapshot.evidence_ticket,
                "provenance": list(snapshot.provenance),
            }
        }

    def _mcp_task(
        self,
        task: StoredTask,
        snapshot: TaskChannelSnapshot | TaskChannelEvent,
        *,
        result_type: str,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "resultType": result_type,
            "taskId": task.task_id,
            "status": snapshot.status.value,
            "createdAt": task.created_at,
            "lastUpdatedAt": task.last_updated_at,
            "ttlMs": task.ttl_ms,
            "pollIntervalMs": task.poll_interval_ms,
            "_meta": self._task_metadata(snapshot, task.binding),
        }
        if snapshot.status_message:
            value["statusMessage"] = snapshot.status_message
        if snapshot.status is TaskStatus.INPUT_REQUIRED and snapshot.input_requests is not None:
            value["inputRequests"] = copy.deepcopy(dict(snapshot.input_requests))
        elif snapshot.status is TaskStatus.COMPLETED:
            value["result"] = copy.deepcopy(snapshot.result)
        elif snapshot.status is TaskStatus.FAILED:
            value["error"] = copy.deepcopy(dict(snapshot.error or {}))
        return value

    @staticmethod
    def _task_capability(params: Mapping[str, object]) -> bool:
        meta = params.get("_meta")
        if not isinstance(meta, Mapping):
            return False
        capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
        if not isinstance(capabilities, Mapping):
            return False
        extensions = capabilities.get("extensions")
        return isinstance(extensions, Mapping) and MCP_TASKS_EXTENSION in extensions

    @staticmethod
    def _mcp_meta(params: Mapping[str, object]) -> Mapping[str, object]:
        meta = params.get("_meta")
        if not isinstance(meta, Mapping):
            raise InvalidProtocolRequestError("MCP request is missing per-request _meta")
        if meta.get("io.modelcontextprotocol/protocolVersion") != MCP_PROTOCOL_VERSION:
            raise UnsupportedProtocolVersionError(
                "MCP", str(meta.get("io.modelcontextprotocol/protocolVersion"))
            )
        client = meta.get("io.modelcontextprotocol/clientInfo")
        if (
            not isinstance(client, Mapping)
            or type(client.get("name")) is not str
            or type(client.get("version")) is not str
        ):
            raise InvalidProtocolRequestError("MCP request has invalid clientInfo")
        return meta

    def _validate_mcp_envelope(self, envelope: McpRequestEnvelope) -> tuple[object, str, Mapping[str, object]]:
        if envelope.protocol_version != MCP_PROTOCOL_VERSION:
            raise UnsupportedProtocolVersionError("MCP", envelope.protocol_version)
        body = envelope.body
        if not isinstance(body, Mapping) or body.get("jsonrpc") != "2.0":
            raise InvalidProtocolRequestError("invalid JSON-RPC request", code=-32600)
        request_id = body.get("id")
        if not (
            type(request_id) is str
            or (type(request_id) is int and -_JSONRPC_ID_LIMIT <= request_id <= _JSONRPC_ID_LIMIT)
        ):
            raise InvalidProtocolRequestError("invalid JSON-RPC id", code=-32600)
        method = body.get("method")
        if type(method) is not str or method != envelope.method_header:
            raise InvalidProtocolRequestError(
                "Mcp-Method does not match the JSON-RPC method",
                code=-32020,
                reason="HEADER_MISMATCH",
            )
        params = body.get("params", {})
        if not isinstance(params, Mapping):
            raise InvalidProtocolRequestError("JSON-RPC params must be an object")
        self._mcp_meta(params)
        expected_name: object | None = None
        if method == "tools/call":
            expected_name = params.get("name")
        elif method in ("tasks/get", "tasks/update", "tasks/cancel"):
            expected_name = params.get("taskId")
        if expected_name is not None and envelope.name_header != expected_name:
            raise InvalidProtocolRequestError(
                "Mcp-Name does not match the routed object",
                code=-32020,
                reason="HEADER_MISMATCH",
            )
        if expected_name is None and envelope.name_header is not None:
            raise InvalidProtocolRequestError(
                "Mcp-Name is not valid for this method",
                code=-32020,
                reason="HEADER_MISMATCH",
            )
        return request_id, method, params

    @staticmethod
    def _jsonrpc_result(request_id: object, result: object) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _jsonrpc_error(request_id: object, error: ProtocolGatewayError) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": error.code,
                "message": error.public_message,
                "data": {"reason": error.reason},
            },
        }

    def handle_mcp(
        self, envelope: McpRequestEnvelope, principal: GatewayPrincipal
    ) -> dict[str, object]:
        request_id = envelope.body.get("id") if isinstance(envelope.body, Mapping) else None
        try:
            request_id, method, params = self._validate_mcp_envelope(envelope)
            self._authorize(principal, "mcp", method)
            if method == "server/discover":
                result = {
                    "resultType": "complete",
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"extensions": {MCP_TASKS_EXTENSION: {}}},
                }
            elif method == "tools/list":
                result = self.mcp_tools_list(principal, already_authorized=True)
            elif method == "tools/call":
                result = self.mcp_tools_call(principal, params, already_authorized=True)
            elif method == "tasks/get":
                result = self.mcp_tasks_get(principal, params.get("taskId"), already_authorized=True)
            elif method == "tasks/update":
                result = self.mcp_tasks_update(
                    principal,
                    params.get("taskId"),
                    params.get("inputResponses"),
                    already_authorized=True,
                )
            elif method == "tasks/cancel":
                result = self.mcp_tasks_cancel(
                    principal, params.get("taskId"), already_authorized=True
                )
            else:
                raise InvalidProtocolRequestError(
                    "method not found", code=-32601, reason="METHOD_NOT_FOUND"
                )
            return self._jsonrpc_result(request_id, result)
        except ProtocolGatewayError as exc:
            return self._jsonrpc_error(request_id, exc)

    def mcp_tools_list(
        self, principal: GatewayPrincipal, *, already_authorized: bool = False
    ) -> dict[str, object]:
        if not already_authorized:
            self._authorize(principal, "mcp", "tools/list")
        tools: list[dict[str, object]] = []
        for tool in sorted(self._tools.values(), key=lambda item: item.name):
            item: dict[str, object] = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": copy.deepcopy(dict(tool.input_schema)),
                "_meta": {
                    "io.agentos/remoteSchemaSha256": tool.remote_schema_digest,
                    "io.agentos/kernelManifestSha256": tool.kernel_manifest_digest,
                    "io.agentos/taskMode": tool.task_mode,
                },
            }
            if tool.output_schema is not None:
                item["outputSchema"] = copy.deepcopy(dict(tool.output_schema))
            tools.append(item)
        return {"resultType": "complete", "tools": tools, "ttlMs": 30_000, "cacheScope": "private"}

    def mcp_tools_call(
        self,
        principal: GatewayPrincipal,
        params: Mapping[str, object],
        *,
        already_authorized: bool = False,
    ) -> dict[str, object]:
        if not already_authorized:
            self._authorize(principal, "mcp", "tools/call")
        if not isinstance(params, Mapping):
            raise InvalidProtocolRequestError("tools/call params must be an object")
        tool = self._tool(params.get("name"))
        arguments = params.get("arguments", {})
        self._schema_validator(tool.input_schema, arguments)
        if tool.task_mode == "task" and not self._task_capability(params):
            raise InvalidProtocolRequestError(
                "client did not declare the Tasks extension for this request",
                code=-32003,
                reason="MISSING_REQUIRED_CLIENT_CAPABILITY",
            )

        opaque = self._new_id("task_" if tool.task_mode == "task" else "call_", task=tool.task_mode == "task")
        submission = self._submit(
            tool=tool,
            payload=arguments,
            submission_key=opaque,
            provenance=("CROSS_AGENT_DATA",),
        )
        if tool.task_mode == "sync":
            snapshot = submission.snapshot
            if snapshot.status is TaskStatus.COMPLETED:
                if isinstance(snapshot.result, Mapping):
                    value = copy.deepcopy(dict(snapshot.result))
                    value.setdefault("resultType", "complete")
                    value.setdefault("_meta", self._task_metadata(snapshot, snapshot.binding))
                    return value
                return {
                    "resultType": "complete",
                    "structuredContent": copy.deepcopy(snapshot.result),
                    "_meta": self._task_metadata(snapshot, snapshot.binding),
                }
            if snapshot.status is TaskStatus.FAILED:
                raise ProtocolGatewayError(
                    -32000,
                    "TOOL_EXECUTION_FAILED",
                    str((snapshot.error or {}).get("message", "tool execution failed")),
                )
            raise ProtocolGatewayError(
                -32000,
                "TRANSPORT_CONTRACT_VIOLATION",
                "synchronous tool did not produce a terminal completion",
            )

        context_id = self._new_id("context_", task=False)
        task = self._create_record(
            task_id=opaque,
            principal=principal,
            binding=submission.binding,
            tool=tool,
            protocol="mcp",
            context_id=context_id,
        )
        # The durable mapping exists before CreateTaskResult becomes visible.
        return self._mcp_task(task, submission.snapshot, result_type="task")

    def mcp_tasks_get(
        self,
        principal: GatewayPrincipal,
        task_id: object,
        *,
        already_authorized: bool = False,
    ) -> dict[str, object]:
        if not already_authorized:
            self._authorize(principal, "mcp", "tasks/get")
        task = self._sync_record(self._owned_task(principal, task_id))
        snapshot = self._transport.snapshot(self._channel_issuer, task.binding)
        return self._mcp_task(task, snapshot, result_type="complete")

    def mcp_tasks_update(
        self,
        principal: GatewayPrincipal,
        task_id: object,
        input_responses: object,
        *,
        already_authorized: bool = False,
    ) -> dict[str, object]:
        if not already_authorized:
            self._authorize(principal, "mcp", "tasks/update")
        task = self._owned_task(principal, task_id)
        if not isinstance(input_responses, Mapping):
            raise InvalidProtocolRequestError("inputResponses must be an object")
        _validate_json_tree(input_responses)
        try:
            self._transport.update(self._channel_issuer, task.binding, input_responses)
        except InvalidTaskTransitionError as exc:
            raise InvalidProtocolRequestError(str(exc), reason="INVALID_TASK_STATE") from exc
        self._sync_record(task)
        return {"resultType": "complete"}

    def mcp_tasks_cancel(
        self,
        principal: GatewayPrincipal,
        task_id: object,
        *,
        already_authorized: bool = False,
    ) -> dict[str, object]:
        if not already_authorized:
            self._authorize(principal, "mcp", "tasks/cancel")
        task = self._owned_task(principal, task_id)
        self._transport.cancel(self._channel_issuer, task.binding)
        self._sync_record(task)
        return {"resultType": "complete"}

    def mcp_task_notifications(
        self,
        principal: GatewayPrincipal,
        *,
        task_ids: tuple[str, ...] | None = None,
        after_sequence: int = 0,
    ) -> tuple[dict[str, object], ...]:
        self._authorize(principal, "mcp", "subscriptions/listen")
        if type(after_sequence) is not int or after_sequence < 0:
            raise InvalidProtocolRequestError("after_sequence must be non-negative")
        if task_ids is None:
            tasks = self._store.list_owner(principal.owner_key)
        else:
            tasks = tuple(self._owned_task(principal, task_id) for task_id in task_ids)
        pending: list[tuple[int, StoredTask, TaskChannelEvent]] = []
        for task in tasks:
            for event in self._transport.events(
                self._channel_issuer, task.binding, after_sequence=after_sequence
            ):
                if event.kind is TaskEventKind.STATUS:
                    pending.append((event.sequence, task, event))
        pending.sort(key=lambda item: item[0])
        return tuple(
            {
                "jsonrpc": "2.0",
                "method": "notifications/tasks",
                "params": self._mcp_task(task, event, result_type="complete"),
            }
            for _, task, event in pending
        )

    def _validate_a2a_envelope(self, envelope: A2ARequestEnvelope, principal: GatewayPrincipal) -> None:
        if envelope.protocol_version != A2A_PROTOCOL_VERSION:
            raise UnsupportedProtocolVersionError("A2A", envelope.protocol_version)
        if not envelope.tenant or envelope.tenant != principal.tenant:
            raise UnauthorizedGatewayError("A2A tenant does not match the authenticated identity")
        if self._a2a_tenant is not None and envelope.tenant != self._a2a_tenant:
            raise UnauthorizedGatewayError("A2A tenant does not match the selected interface")
        if not isinstance(envelope.body, Mapping):
            raise InvalidProtocolRequestError("A2A request body must be an object")
        self._authorize(principal, "a2a", envelope.operation)

    @staticmethod
    def _validate_a2a_message(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise InvalidProtocolRequestError("A2A message must be an object")
        if type(value.get("messageId")) is not str or not value["messageId"]:
            raise InvalidProtocolRequestError("A2A messageId is required")
        if value.get("role") != "ROLE_USER":
            raise InvalidProtocolRequestError("A2A v1 client message role must be ROLE_USER")
        parts = value.get("parts")
        if not isinstance(parts, list) or not parts:
            raise InvalidProtocolRequestError("A2A message requires at least one Part")
        for part in parts:
            if not isinstance(part, Mapping):
                raise InvalidProtocolRequestError("A2A Part must be an object")
            members = [name for name in ("text", "raw", "url", "data") if name in part]
            if len(members) != 1 or "kind" in part or "file" in part:
                raise InvalidProtocolRequestError(
                    "A2A v1 Part must use exactly one member-based content field"
                )
        _validate_json_tree(value)
        return copy.deepcopy(dict(value))

    @staticmethod
    def _a2a_state(status: TaskStatus) -> str:
        return {
            TaskStatus.WORKING: "TASK_STATE_WORKING",
            TaskStatus.INPUT_REQUIRED: "TASK_STATE_INPUT_REQUIRED",
            TaskStatus.COMPLETED: "TASK_STATE_COMPLETED",
            TaskStatus.FAILED: "TASK_STATE_FAILED",
            TaskStatus.CANCELLED: "TASK_STATE_CANCELED",
        }[status]

    def _ensure_result_artifact(
        self, task: StoredTask, snapshot: TaskChannelSnapshot
    ) -> StoredTask:
        if snapshot.status is not TaskStatus.COMPLETED or task.artifacts or snapshot.result is None:
            return task
        if isinstance(snapshot.result, Mapping) and isinstance(snapshot.result.get("artifacts"), list):
            artifacts = tuple(copy.deepcopy(snapshot.result["artifacts"]))
        else:
            artifact_id = self._new_id("artifact_", task=False)
            if type(snapshot.result) is str:
                part: dict[str, object] = {"text": snapshot.result, "mediaType": "text/plain"}
            else:
                part = {"data": copy.deepcopy(snapshot.result), "mediaType": "application/json"}
            artifact: dict[str, object] = {
                "artifactId": artifact_id,
                "name": "result",
                "parts": [part],
                "metadata": self._task_metadata(snapshot, task.binding),
            }
            if snapshot.result_handle is not None:
                artifact["metadata"][AGENTOS_TASK_METADATA]["resultHandle"] = {  # type: ignore[index]
                    "slot": snapshot.result_handle.slot,
                    "generation": snapshot.result_handle.generation,
                    "owned": snapshot.result_handle.owned,
                }
            artifacts = (artifact,)
        updated = replace(task, artifacts=artifacts, last_updated_at=_iso8601(self._clock))
        self._store.replace(updated)
        return updated

    def _a2a_task(self, task: StoredTask, snapshot: TaskChannelSnapshot) -> dict[str, object]:
        task = self._ensure_result_artifact(task, snapshot)
        status: dict[str, object] = {
            "state": self._a2a_state(snapshot.status),
            "timestamp": task.last_updated_at,
        }
        if snapshot.status_message:
            status["message"] = {
                "messageId": f"status-{snapshot.latest_sequence}",
                "contextId": task.context_id,
                "taskId": task.task_id,
                "role": "ROLE_AGENT",
                "parts": [{"text": snapshot.status_message, "mediaType": "text/plain"}],
            }
        return {
            "id": task.task_id,
            "contextId": task.context_id,
            "status": status,
            "artifacts": copy.deepcopy(list(task.artifacts)),
            "history": copy.deepcopy(list(task.history)),
            "metadata": self._task_metadata(snapshot, task.binding),
        }

    def a2a_send_message(
        self, envelope: A2ARequestEnvelope, principal: GatewayPrincipal
    ) -> dict[str, object]:
        self._validate_a2a_envelope(envelope, principal)
        if envelope.operation not in ("SendMessage", "SendStreamingMessage"):
            raise InvalidProtocolRequestError("A2A envelope has the wrong operation")
        message = self._validate_a2a_message(envelope.body.get("message"))
        existing_id = message.get("taskId")
        if existing_id is not None:
            task = self._owned_task(principal, existing_id)
            snapshot = self._transport.snapshot(self._channel_issuer, task.binding)
            if snapshot.status is not TaskStatus.INPUT_REQUIRED:
                raise InvalidProtocolRequestError(
                    "follow-up message requires an input-required Task",
                    reason="INVALID_TASK_STATE",
                )
            outstanding = snapshot.input_requests or {}
            if not outstanding:
                raise InvalidProtocolRequestError("input-required Task has no request key")
            first_key = next(iter(outstanding))
            self._transport.update(
                self._channel_issuer, task.binding, {first_key: copy.deepcopy(message)}
            )
            updated = replace(
                task,
                history=task.history + (message,),
                last_updated_at=_iso8601(self._clock),
            )
            self._store.replace(updated)
            return self._a2a_task(
                updated, self._transport.snapshot(self._channel_issuer, updated.binding)
            )

        tool_name = self._a2a_router(message)
        tool = self._tool(tool_name)
        context = message.get("contextId")
        if context is None:
            context = self._new_id("context_", task=False)
        elif type(context) is not str or _ID_RE.fullmatch(context) is None:
            raise InvalidProtocolRequestError("A2A contextId is not canonical")
        provenance = ["CROSS_AGENT_DATA"]
        parts = message.get("parts", [])
        if any(isinstance(part, Mapping) and "url" in part for part in parts):
            provenance.append("UNTRUSTED_FILE_DATA")
        task_id = self._new_id("task_", task=True)
        submission = self._submit(
            tool=tool,
            payload={"message": message},
            submission_key=task_id,
            provenance=tuple(provenance),
        )
        task = self._create_record(
            task_id=task_id,
            principal=principal,
            binding=submission.binding,
            tool=tool,
            protocol="a2a",
            context_id=context,
            history=(message,),
        )
        return self._a2a_task(task, submission.snapshot)

    def a2a_get_task(
        self, envelope: A2ARequestEnvelope, principal: GatewayPrincipal
    ) -> dict[str, object]:
        self._validate_a2a_envelope(envelope, principal)
        if envelope.operation != "GetTask":
            raise InvalidProtocolRequestError("A2A envelope has the wrong operation")
        task = self._sync_record(self._owned_task(principal, envelope.body.get("id")))
        snapshot = self._transport.snapshot(self._channel_issuer, task.binding)
        return self._a2a_task(task, snapshot)

    def a2a_cancel_task(
        self, envelope: A2ARequestEnvelope, principal: GatewayPrincipal
    ) -> dict[str, object]:
        self._validate_a2a_envelope(envelope, principal)
        if envelope.operation != "CancelTask":
            raise InvalidProtocolRequestError("A2A envelope has the wrong operation")
        task = self._owned_task(principal, envelope.body.get("id"))
        snapshot = self._transport.cancel(self._channel_issuer, task.binding)
        task = self._sync_record(task)
        return self._a2a_task(task, snapshot)

    def a2a_stream(
        self,
        envelope: A2ARequestEnvelope,
        principal: GatewayPrincipal,
        *,
        after_sequence: int = 0,
        include_snapshot: bool = True,
    ) -> tuple[dict[str, object], ...]:
        self._validate_a2a_envelope(envelope, principal)
        if envelope.operation != "SubscribeToTask":
            raise InvalidProtocolRequestError("A2A envelope has the wrong operation")
        task = self._owned_task(principal, envelope.body.get("id"))
        snapshot = self._transport.snapshot(self._channel_issuer, task.binding)
        if snapshot.status in TERMINAL_TASK_STATUSES:
            raise InvalidProtocolRequestError(
                "cannot subscribe to a terminal Task", reason="UNSUPPORTED_OPERATION"
            )
        values: list[tuple[int, dict[str, object]]] = []
        if include_snapshot:
            values.append((after_sequence, {"task": self._a2a_task(task, snapshot)}))
        for event in self._transport.events(
            self._channel_issuer, task.binding, after_sequence=after_sequence
        ):
            metadata = self._task_metadata(event, task.binding)
            metadata[AGENTOS_TASK_METADATA]["eventSequence"] = event.sequence  # type: ignore[index]
            if event.kind is TaskEventKind.ARTIFACT:
                values.append(
                    (
                        event.sequence,
                        {
                            "artifactUpdate": {
                                "taskId": task.task_id,
                                "contextId": task.context_id,
                                "artifact": copy.deepcopy(dict(event.artifact or {})),
                                "index": event.artifact_index,
                                "append": event.append,
                                "lastChunk": event.last_chunk,
                                "metadata": metadata,
                            }
                        },
                    )
                )
            else:
                status: dict[str, object] = {"state": self._a2a_state(event.status)}
                if event.status_message:
                    status["message"] = {
                        "messageId": f"status-{event.sequence}",
                        "contextId": task.context_id,
                        "taskId": task.task_id,
                        "role": "ROLE_AGENT",
                        "parts": [{"text": event.status_message, "mediaType": "text/plain"}],
                    }
                values.append(
                    (
                        event.sequence,
                        {
                            "statusUpdate": {
                                "taskId": task.task_id,
                                "contextId": task.context_id,
                                "status": status,
                                "metadata": metadata,
                            }
                        },
                    )
                )
        # The initial Task is first; transport events retain the CQ global order.
        head = values[:1] if include_snapshot else []
        tail = values[1:] if include_snapshot else values
        tail.sort(key=lambda item: item[0])
        return tuple(value for _, value in head + tail)


__all__ = [
    "A2A_PROTOCOL_VERSION",
    "A2ARequestEnvelope",
    "AGENTOS_TASK_METADATA",
    "GatewayPrincipal",
    "InMemoryTaskStore",
    "InvalidProtocolRequestError",
    "MCP_PROTOCOL_VERSION",
    "MCP_TASKS_EXTENSION",
    "McpA2AGateway",
    "McpRequestEnvelope",
    "ProtocolGatewayError",
    "StaticIssuerPolicy",
    "StoredTask",
    "TaskNotFoundError",
    "TaskStore",
    "ToolManifest",
    "UnauthorizedGatewayError",
    "UnsupportedProtocolVersionError",
    "canonical_json_bytes",
    "canonical_schema_digest",
]
