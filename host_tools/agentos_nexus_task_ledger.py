#!/usr/bin/env python3
"""Bounded Host-side state machine for Nexus TASK_EVENT proofs.

The relay validates wire types before calling this module.  This ledger adds
the stateful guarantees which cannot be established from one event alone:
turn/lifecycle binding, the root/child DAG, stable kernel identities, strict
task transitions, and Task/Tool/artifact cross-bindings.

Only bounded metadata and hashes are retained.  In particular, TASK_EVENT
``summary`` text and tool arguments are hashed at the API boundary and are
never stored in the ledger or exposed by snapshots.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
from typing import Final


MAX_U32: Final = (1 << 32) - 1
MAX_U64: Final = (1 << 63) - 1
FULL_U64_MAX: Final = (1 << 64) - 1
MIN_I32: Final = -(1 << 31)
MAX_I32: Final = (1 << 31) - 1
AGENT_STATUS_OK: Final = 0
AGENT_STATUS_NO_SPACE: Final = -6
AGENT_STATUS_TIMEOUT: Final = -7
AGENT_STATUS_CANCELLED: Final = -10
AGENT_STATUS_IO_ERROR: Final = -18
AGENT_STATUS_INDETERMINATE: Final = -20
# Compatibility alias for the first standalone ledger API draft.
AGENT_STATUS_TASK_FAILED: Final = AGENT_STATUS_INDETERMINATE
NEXUS_PROVENANCE_ALL: Final = (1 << 6) - 1
NEXUS_ROOT_TASK_BASE: Final = 100
NEXUS_PROVENANCE_FAILURE: Final = 0
FILE_READ_MAX_LINES: Final = 64
TOOL_ARGUMENT_STRING_BYTES: Final = 3072

DEFAULT_MAX_TASKS: Final = 17
DEFAULT_MAX_EVENTS: Final = 4096
DEFAULT_MAX_EVENTS_PER_TASK: Final = 256
ABSOLUTE_MAX_TASKS: Final = 256
ABSOLUTE_MAX_EVENTS: Final = 65536

TASK_TOOL_ROLES: Final[Mapping[str, str]] = {
    "search_files": "research",
    "read_file": "research",
    "inspect_system": "system",
    "write_file": "research",
    "apply_patch": "research",
    "build_ucore_program": "research",
    "run_ucore_program": "research",
}
TASK_ARTIFACT_PROVENANCE: Final[Mapping[str, int]] = {
    "search_files": 60,
    "read_file": 60,
    "inspect_system": 53,
    "write_file": 60,
    "apply_patch": 60,
    "build_ucore_program": 60,
    "run_ucore_program": 60,
}
TOOL_PROVENANCE: Final[Mapping[str, int]] = {
    "search_files": 60,
    "read_file": 60,
    "inspect_system": 53,
    "write_file": 60,
    "apply_patch": 60,
    "build_ucore_program": 60,
    "run_ucore_program": 60,
}
DEVELOPMENT_TOOLS: Final = frozenset(
    ("write_file", "apply_patch", "build_ucore_program", "run_ucore_program")
)
WORKSPACE_TOOLS: Final = frozenset(("search_files", "read_file", *DEVELOPMENT_TOOLS))
WORKSPACE_OPERATIONS: Final[Mapping[str, frozenset[str]]] = {
    "search_files": frozenset(("manifest", "search")),
    "read_file": frozenset(("manifest", "read")),
    "write_file": frozenset(("write",)),
    "apply_patch": frozenset(("patch",)),
    "build_ucore_program": frozenset(("build",)),
    "run_ucore_program": frozenset(("run",)),
}
WORKSPACE_RESULT_STATUSES: Final = frozenset(("ok", "stale", "error"))
# Three complete 10k-object scans fit even when bounded manifests shrink to
# nine entries for maximum-size UTF-8 paths.
MAX_WORKSPACE_ATTEMPTS: Final = 8192
MAX_WORKSPACE_CONTENT_BYTES: Final = 2800
MAX_WORKSPACE_MANIFEST_BYTES: Final = 12000
# event, root status, TURN_COMPLETE status
TERMINATION_CONTRACTS: Final[Mapping[str, tuple[str, int, str]]] = {
    "user_interrupt": ("cancelled", AGENT_STATUS_CANCELLED, "cancelled"),
    "provider_fatal": ("failed", AGENT_STATUS_IO_ERROR, "error"),
    "round_limit": ("cancelled", AGENT_STATUS_CANCELLED, "cancelled"),
    "session_error": ("failed", AGENT_STATUS_IO_ERROR, "error"),
    "context_final_failed": ("failed", AGENT_STATUS_NO_SPACE, "error"),
}
BUSINESS_ROLES: Final = frozenset(("coordinator", "system", "research"))
TASK_TERMINALS: Final = frozenset(("completed", "failed", "cancelled"))
TASK_EVENTS: Final = frozenset(
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
TASK_STATES: Final = frozenset(
    ("assigned", "accepted", "running", "waiting", "completed", "failed", "cancelled")
)

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOOL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_PROGRAM_PATH_RE = re.compile(r"user/src/nexus_[a-z][a-z0-9_]{0,31}_ucore\.c\Z")
_TARGET_RE = re.compile(r"nexus_[a-z][a-z0-9_]{0,31}_ucore\Z")
_SESSION_BLOCK_SUMMARY = "worker_not_quiescent;session_blocked=1"
_SESSION_BLOCK_SUMMARY_SHA256 = hashlib.sha256(
    _SESSION_BLOCK_SUMMARY.encode("utf-8")
).hexdigest()
_ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT = (
    "artifact_cleanup_failed;session_blocked=1"
)
_ARTIFACT_CLEANUP_SESSION_BLOCK_SHA256 = hashlib.sha256(
    _ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT.encode("utf-8")
).hexdigest()
_CONTEXT_FINAL_FAILED_SUMMARY = "context_final_failed"
_CONTEXT_FINAL_FAILED_SHA256 = hashlib.sha256(
    _CONTEXT_FINAL_FAILED_SUMMARY.encode("utf-8")
).hexdigest()
_TASK_CHANNEL_SUMMARY_RE = re.compile(
    r"^task_channel_v1;phase=(assigned|cqe);"
    r"channel_generation=([1-9][0-9]{0,19});"
    r"request_id=([1-9][0-9]{0,19});"
    r"slot_generation=([1-9][0-9]{0,19});"
    r"tool_id=26;contract_generation=([1-9][0-9]{0,19})$"
)

_EVENT_REQUIRED_FIELDS = frozenset(
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
_EVENT_OPTIONAL_FIELDS = frozenset(
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
        # Host-normalized aliases.  If both forms occur they must agree.
        "agent_role",
        "agent_control_id",
        "artifact_sha256",
        "type",
    )
)

_EVENT_HASH_FIELDS = (
    "version",
    "corr_id",
    "event",
    "task_state",
    "status",
    "tick",
    "deadline_tick",
    "context_seq",
    "metric_code",
    "metric_value",
    "artifact_handle",
    "artifact_sha256",
    "resource_used",
    "provenance",
    "source_pid",
    "target_pid",
    "summary_bytes",
    "summary_sha256",
)

_TASK_ROOT_ITEM_FIELDS = (
    "version",
    "turn_id",
    "request_id",
    "workflow_lifecycle_id",
    "workflow_lifecycle_generation",
    "task_id",
    "parent_task_id",
    "assigned_corr_id",
    "terminal_corr_id",
    "role",
    "agent_pid",
    "agent_id",
    "control_id_known",
    "control_id",
    "deadline_tick",
    "terminal_event",
    "terminal_status",
    "terminal_context_seq",
    "artifact_handle",
    "artifact_sha256",
    "artifact_context_seq",
    "resource_used",
    "provenance",
    "tool",
    "arguments_sha256",
    "projection_sha256",
    "result_sha256",
    "event_count",
    "event_sha256",
)

_ARTIFACT_ROOT_ITEM_FIELDS = (
    "version",
    "turn_id",
    "request_id",
    "workflow_lifecycle_id",
    "workflow_lifecycle_generation",
    "task_id",
    "parent_task_id",
    "corr_id",
    "role",
    "agent_pid",
    "agent_id",
    "control_id",
    "tool",
    "artifact_handle",
    "artifact_sha256",
    "cqe_context_seq",
    "consumption_context_seq",
    "resource_used",
    "provenance",
    "projection_sha256",
)

_TOOL_ROOT_ITEM_FIELDS = (
    "version",
    "turn_id",
    "request_id",
    "corr_id",
    "tool",
    "arguments_sha256",
    "argument_binding_sha256",
    "task_id",
    "settled",
    "status",
    "value0",
    "value1",
    "value2",
    "provenance",
    "projection_sha256",
    "result_sha256",
    "context_seq",
    "workspace_source_sha256",
    "workspace_attempt_count",
    "workspace_attempts_sha256",
    "session_blocked",
)

_WORKSPACE_ATTEMPT_FIELDS = (
    "version",
    "corr_id",
    "task_id",
    "tool",
    "operation",
    "attempt",
    "manifest_cursor",
    "manifest_next_cursor",
    "manifest_eof",
    "request_generation",
    "result_generation",
    "arguments_sha256",
    "request_objects_sha256",
    "result_objects_sha256",
    "request_sha256",
    "status",
    "content_bytes",
    "content_sha256",
    "result_sha256",
)


class NexusTaskLedgerError(ValueError):
    """A stateful Nexus Task proof invariant was violated."""

    def __init__(self, reason: str, *, code: str = "BAD_TASK_EVENT") -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class KernelIdentity:
    """One identity authenticated by Host-observed kernel telemetry."""

    role: str
    pid: int
    agent_id: int
    control_id: int


@dataclass(frozen=True, slots=True)
class NexusTaskSnapshot:
    """Immutable, body-free projection of one Task."""

    task_id: int
    parent_task_id: int
    assigned_corr_id: int
    terminal_corr_id: int
    role: str
    agent_pid: int
    agent_id: int
    control_id_known: bool
    control_id: int
    deadline_tick: int
    state: str
    terminal_event: str
    terminal_status: int
    terminal_context_seq: int
    artifact_handle: int
    artifact_sha256: str
    artifact_context_seq: int
    resource_used: int
    provenance: int
    tool: str
    identity_verified: bool
    event_count: int
    event_sha256: str


@dataclass(frozen=True, slots=True)
class NexusTaskLedgerSnapshot:
    """Immutable snapshot suitable for controller/status projections."""

    active: bool
    sealed: bool
    turn_id: int
    request_id: int
    workflow_lifecycle_id: int
    workflow_lifecycle_generation: int
    provider_final_frozen: bool
    cancelling: bool
    termination_cause: str
    session_blocked: bool
    latest_corr_id: int
    model_request_count: int
    task_count: int
    event_count: int
    delivered_tool_count: int
    settled_tool_count: int
    all_required_terminal: bool
    task_root_sha256: str
    artifact_root_sha256: str
    tasks: tuple[NexusTaskSnapshot, ...]


@dataclass(slots=True)
class _ToolRecord:
    corr_id: int
    tool: str
    arguments_sha256: str
    argument_binding_sha256: str
    task_id: int = 0
    settled: bool = False
    status: int = 0
    value0: int = 0
    value1: int = 0
    value2: int = 0
    provenance: int = 0
    projection_sha256: str = ""
    result_sha256: str = ""
    context_seq: int = 0
    workspace_source_sha256: str = ""
    reserved_task_id: int = 0
    workspace_attempts: list["_WorkspaceAttempt"] = field(default_factory=list)
    session_blocked: bool = False


@dataclass(slots=True)
class _WorkspaceAttempt:
    corr_id: int
    task_id: int
    tool: str
    operation: str
    attempt: int
    manifest_cursor: int
    request_generation: str
    arguments_sha256: str
    request_objects_sha256: str
    request_sha256: str
    status: str = ""
    manifest_next_cursor: int = 0
    manifest_eof: bool = False
    result_generation: str = ""
    result_objects_sha256: str = ""
    content_bytes: int = 0
    content_sha256: str = ""
    result_sha256: str = ""


@dataclass(slots=True)
class _RequestRecord:
    corr_id: int
    observed: bool = True
    response_outcome: str = ""
    tool_settled: bool = False
    termination_cause: str = ""


@dataclass(slots=True)
class _TaskRecord:
    turn_id: int
    request_id: int
    lifecycle_id: int
    lifecycle_generation: int
    task_id: int
    parent_task_id: int
    assigned_corr_id: int
    role: str
    agent_pid: int
    agent_id: int
    control_id_known: bool
    control_id: int
    deadline_tick: int
    channel_generation: int = 0
    channel_request_id: int = 0
    slot_generation: int = 0
    contract_generation: int = 0
    state: str = "assigned"
    terminal_event: str = ""
    terminal_corr_id: int = 0
    terminal_status: int = 0
    terminal_context_seq: int = 0
    artifact_handle: int = 0
    artifact_sha256: str = ""
    artifact_context_seq: int = 0
    resource_used: int = 0
    provenance: int = 0
    identity_verified: bool = False
    progress_count: int = 0
    events: list[dict[str, object]] = field(default_factory=list)


def canonical_json_bytes(value: object) -> bytes:
    """Encode fixed-order proof data using the Nexus Host convention."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
    code: str = "BAD_TASK_EVENT",
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise NexusTaskLedgerError(
            f"{label} is outside its valid range", code=code
        )
    return value


def _text(
    value: object,
    label: str,
    *,
    maximum: int,
    empty: bool = False,
    code: str = "BAD_TASK_EVENT",
) -> str:
    if not isinstance(value, str):
        raise NexusTaskLedgerError(f"{label} must be text", code=code)
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NexusTaskLedgerError(f"{label} is not UTF-8", code=code) from error
    if (not empty and not raw) or len(raw) > maximum:
        raise NexusTaskLedgerError(f"{label} is outside its size bound", code=code)
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value):
        raise NexusTaskLedgerError(f"{label} contains control characters", code=code)
    return value


def _digest(
    value: object,
    label: str,
    *,
    empty: bool = False,
    code: str = "BAD_TASK_EVENT",
) -> str:
    if empty and value == "":
        return ""
    result = _text(value, label, maximum=64, code=code)
    if _DIGEST_RE.fullmatch(result) is None:
        raise NexusTaskLedgerError(f"{label} is malformed", code=code)
    return result


def _task_channel_binding(
    summary: str, *, phase: str
) -> tuple[int, int, int, int]:
    match = _TASK_CHANNEL_SUMMARY_RE.fullmatch(summary)
    if match is None or match.group(1) != phase:
        raise NexusTaskLedgerError(
            "child TASK_EVENT lacks its Task Channel binding"
        )
    values = tuple(
        _integer(
            int(match.group(index)),
            "Task Channel binding",
            minimum=1,
            maximum=FULL_U64_MAX,
        )
        for index in range(2, 6)
    )
    return values[0], values[1], values[2], values[3]


def _fixed(fields: Sequence[str], values: Mapping[str, object]) -> dict[str, object]:
    if set(values) != set(fields):
        raise AssertionError("internal canonical proof fields changed")
    return {field: values[field] for field in fields}


def _projection_field(content: str, name: str) -> str:
    prefix = f"{name}="
    for line in content.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return ""


def _json_object(text: str, *, code: str) -> dict[str, object]:
    """Parse one JSON object while rejecting duplicate keys and non-finite data."""

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise NexusTaskLedgerError(
                    "canonical tool arguments contain a duplicate key", code=code
                )
            result[key] = value
        return result

    def constant(_value: str) -> object:
        raise NexusTaskLedgerError(
            "canonical tool arguments contain a non-finite number", code=code
        )

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except NexusTaskLedgerError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise NexusTaskLedgerError(
            "canonical tool arguments are malformed JSON", code=code
        ) from error
    if not isinstance(value, dict):
        raise NexusTaskLedgerError(
            "canonical tool arguments must be an object", code=code
        )
    return value


def _argument_text(
    value: object,
    label: str,
    *,
    maximum: int,
    empty: bool = False,
) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise NexusTaskLedgerError(f"{label} must be text", code="BAD_TOOL_EVENT")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NexusTaskLedgerError(
            f"{label} is not UTF-8", code="BAD_TOOL_EVENT"
        ) from error
    escaped_bytes = sum(
        6 if byte < 0x20 else (2 if byte in (ord('"'), ord("\\")) else 1)
        for byte in raw
    )
    if (
        (not empty and not value)
        or len(value) > maximum
        or len(raw) > TOOL_ARGUMENT_STRING_BYTES
        or escaped_bytes > TOOL_ARGUMENT_STRING_BYTES
    ):
        raise NexusTaskLedgerError(
            f"{label} is outside its size bound", code="BAD_TOOL_EVENT"
        )
    return value


def _tool_argument_binding(
    tool: str, arguments: Mapping[str, object]
) -> str:
    """Return a body-free binding hash for one v3 tool request."""

    keys = set(arguments)
    binding: dict[str, object]
    if tool == "search_files":
        if not {"query"} <= keys <= {"query", "path_prefix"}:
            raise NexusTaskLedgerError(
                "search_files arguments are malformed", code="BAD_TOOL_EVENT"
            )
        query = _argument_text(
            arguments["query"], "query", maximum=95, empty=True
        )
        prefix = _argument_text(
            arguments.get("path_prefix", ""), "path_prefix", maximum=111, empty=True
        )
        binding = {"tool": tool, "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                   "path_prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest()}
    elif tool == "read_file":
        if keys != {"path", "start_line", "max_lines"}:
            raise NexusTaskLedgerError(
                "read_file arguments are malformed", code="BAD_TOOL_EVENT"
            )
        path = _argument_text(arguments["path"], "path", maximum=255)
        start_line = arguments["start_line"]
        max_lines = arguments["max_lines"]
        start = _integer(
            start_line, "start_line", minimum=1, maximum=MAX_U32,
            code="BAD_TOOL_EVENT"
        )
        count = _integer(
            max_lines, "max_lines", minimum=1, maximum=FILE_READ_MAX_LINES,
            code="BAD_TOOL_EVENT"
        )
        binding = {"tool": tool, "path_sha256": hashlib.sha256(path.encode()).hexdigest(),
                   "start_line": start, "max_lines": count}
    elif tool == "inspect_system":
        if keys != {"operation"} or arguments.get("operation") not in (
            "status", "processes", "context"
        ):
            raise NexusTaskLedgerError(
                "inspect_system arguments are malformed", code="BAD_TOOL_EVENT"
            )
        binding = {"tool": tool, "operation": arguments["operation"]}
    elif tool in ("write_file", "apply_patch"):
        body_name = "content" if tool == "write_file" else "patch"
        if keys != {"path", body_name, "expected_revision"}:
            raise NexusTaskLedgerError(
                f"{tool} arguments are malformed", code="BAD_TOOL_EVENT"
            )
        path = _argument_text(arguments["path"], "path", maximum=64)
        body = _argument_text(
            arguments[body_name], body_name, maximum=2400, empty=True
        )
        revision = _argument_text(
            arguments["expected_revision"], "expected_revision", maximum=64
        )
        if (
            _PROGRAM_PATH_RE.fullmatch(path) is None
            or (revision != "missing" and _DIGEST_RE.fullmatch(revision) is None)
        ):
            raise NexusTaskLedgerError(
                f"{tool} arguments are outside policy", code="BAD_TOOL_EVENT"
            )
        binding = {
            "tool": tool,
            "path": path,
            f"{body_name}_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "expected_revision": revision,
        }
    elif tool == "build_ucore_program":
        if keys != {"source_path", "target"}:
            raise NexusTaskLedgerError(
                "build_ucore_program arguments are malformed", code="BAD_TOOL_EVENT"
            )
        source_path = _argument_text(
            arguments["source_path"], "source_path", maximum=64
        )
        target = _argument_text(arguments["target"], "target", maximum=48)
        if (
            _PROGRAM_PATH_RE.fullmatch(source_path) is None
            or _TARGET_RE.fullmatch(target) is None
            or source_path.rsplit("/", 1)[-1][:-2] != target
        ):
            raise NexusTaskLedgerError(
                "build_ucore_program target is outside policy",
                code="BAD_TOOL_EVENT",
            )
        binding = {"tool": tool, "source_path": source_path, "target": target}
    elif tool == "run_ucore_program":
        if keys != {
            "build_id",
            "stdin",
            "expected_output",
            "expected_exit",
            "case_kind",
        }:
            raise NexusTaskLedgerError(
                "run_ucore_program arguments are malformed", code="BAD_TOOL_EVENT"
            )
        build_id = _argument_text(arguments["build_id"], "build_id", maximum=64)
        stdin = _argument_text(arguments["stdin"], "stdin", maximum=512, empty=True)
        expected_output = _argument_text(
            arguments["expected_output"], "expected_output", maximum=512, empty=True
        )
        expected_exit = _integer(
            arguments["expected_exit"], "expected_exit", minimum=0, maximum=255,
            code="BAD_TOOL_EVENT",
        )
        case_kind = _argument_text(
            arguments["case_kind"], "case_kind", maximum=7
        )
        if _DIGEST_RE.fullmatch(build_id) is None or case_kind not in (
            "normal", "invalid", "failure"
        ):
            raise NexusTaskLedgerError(
                "run_ucore_program arguments are outside policy",
                code="BAD_TOOL_EVENT",
            )
        binding = {
            "tool": tool,
            "build_id": build_id,
            "stdin_sha256": hashlib.sha256(stdin.encode()).hexdigest(),
            "expected_output_sha256": hashlib.sha256(expected_output.encode()).hexdigest(),
            "expected_exit": expected_exit,
            "case_kind": case_kind,
        }
    else:
        raise NexusTaskLedgerError(
            "tool is outside the Nexus contract", code="BAD_TOOL_EVENT"
        )
    return _sha256(binding)


class NexusTaskLedger:
    """A bounded, per-turn Nexus TASK_EVENT proof ledger.

    ``identity_lookup`` receives a PID.  It may return ``KernelIdentity``, the
    relayer's ``(role, pid, agent_id, control_id)`` tuple, an equivalent
    mapping, or ``None`` while kernel telemetry has not arrived yet.

    ``allow_post_final_root_bootstrap`` exists only to make the production
    policy explicit.  The current Guest emits its root prelude before the first
    MODEL_REQUEST; permissive compatibility mode is intentionally unsupported
    so post-final bootstrap data can never enter a proof root.
    """

    def __init__(
        self,
        *,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_events_per_task: int = DEFAULT_MAX_EVENTS_PER_TASK,
        identity_lookup: Callable[[int], object | None] | None = None,
        require_kernel_identity: bool = True,
        task_tool_roles: Mapping[str, str] | None = None,
        allow_post_final_root_bootstrap: bool = False,
    ) -> None:
        self.max_tasks = _integer(
            max_tasks,
            "max_tasks",
            minimum=1,
            maximum=ABSOLUTE_MAX_TASKS,
            code="BAD_LEDGER_CONFIG",
        )
        self.max_events = _integer(
            max_events,
            "max_events",
            minimum=1,
            maximum=ABSOLUTE_MAX_EVENTS,
            code="BAD_LEDGER_CONFIG",
        )
        self.max_events_per_task = _integer(
            max_events_per_task,
            "max_events_per_task",
            minimum=1,
            maximum=ABSOLUTE_MAX_EVENTS,
            code="BAD_LEDGER_CONFIG",
        )
        if self.max_events_per_task > self.max_events:
            raise NexusTaskLedgerError(
                "max_events_per_task exceeds max_events", code="BAD_LEDGER_CONFIG"
            )
        if identity_lookup is not None and not callable(identity_lookup):
            raise NexusTaskLedgerError(
                "identity_lookup must be callable", code="BAD_LEDGER_CONFIG"
            )
        if not isinstance(require_kernel_identity, bool):
            raise NexusTaskLedgerError(
                "require_kernel_identity must be boolean", code="BAD_LEDGER_CONFIG"
            )
        if allow_post_final_root_bootstrap is not False:
            raise NexusTaskLedgerError(
                "post-final root bootstrap is not evidence-safe",
                code="BAD_LEDGER_CONFIG",
            )
        roles = dict(TASK_TOOL_ROLES if task_tool_roles is None else task_tool_roles)
        for tool, role in roles.items():
            if (
                not isinstance(tool, str)
                or _TOOL_RE.fullmatch(tool) is None
                or role not in BUSINESS_ROLES - {"coordinator"}
            ):
                raise NexusTaskLedgerError(
                    "task_tool_roles is malformed", code="BAD_LEDGER_CONFIG"
                )
        self._task_tool_roles = roles
        self._identity_lookup = identity_lookup
        self._require_kernel_identity = require_kernel_identity
        self._kernel_identities: dict[int, KernelIdentity] = {}
        self._role_identities: dict[str, KernelIdentity] = {}
        self._session_lifecycle: tuple[int, int] | None = None
        self._session_blocked = False
        self._clear_turn()

    @property
    def active(self) -> bool:
        return self._turn_id != 0

    @property
    def all_required_terminal(self) -> bool:
        return self._all_required_terminal()

    @property
    def task_root_sha256(self) -> str:
        return self._task_root_sha256

    @property
    def artifact_root_sha256(self) -> str:
        return self._artifact_root_sha256

    @property
    def workflow_lifecycle(self) -> tuple[int, int] | None:
        return self._session_lifecycle

    @property
    def cancelled_cleanup_pending_corr(self) -> int:
        """Return the exact staged no-TOOL cleanup candidate, if any."""

        return self._pending_cancel_derived_cleanup_corr

    def begin_turn(
        self,
        turn_id: object,
        request_id: object,
        *,
        workflow_lifecycle_id: object | None = None,
        workflow_lifecycle_generation: object | None = None,
    ) -> None:
        """Start a fresh active envelope without discarding session identity."""

        if self.active:
            raise NexusTaskLedgerError("a Nexus turn is already active", code="BAD_TURN")
        if self._session_blocked:
            raise NexusTaskLedgerError(
                "the Nexus session is blocked by an indeterminate worker",
                code="BAD_TURN",
            )
        turn = _integer(turn_id, "turn_id", minimum=1, maximum=MAX_U64, code="BAD_TURN")
        request = _integer(
            request_id, "request_id", minimum=1, maximum=MAX_U64, code="BAD_TURN"
        )
        if turn > MAX_U32 - NEXUS_ROOT_TASK_BASE:
            raise NexusTaskLedgerError(
                "turn_id cannot produce a canonical root task_id", code="BAD_TURN"
            )
        if (workflow_lifecycle_id is None) != (
            workflow_lifecycle_generation is None
        ):
            raise NexusTaskLedgerError(
                "workflow lifecycle fields must be supplied together", code="BAD_TURN"
            )
        self._clear_turn()
        self._turn_id = turn
        self._request_id = request
        if workflow_lifecycle_id is not None:
            lifecycle = (
                _integer(
                    workflow_lifecycle_id,
                    "workflow_lifecycle_id",
                    minimum=1,
                    maximum=MAX_U64,
                    code="BAD_TURN",
                ),
                _integer(
                    workflow_lifecycle_generation,
                    "workflow_lifecycle_generation",
                    minimum=1,
                    maximum=MAX_U64,
                    code="BAD_TURN",
                ),
            )
            self._bind_lifecycle(lifecycle, code="BAD_TURN")
            self._turn_lifecycle = lifecycle

    def record_delivered_tool(
        self,
        corr_id: object,
        tool: object,
        *,
        arguments_canonical: object = "",
        arguments_sha256: object | None = None,
    ) -> None:
        """Record a model tool call only after MODEL_RESPONSE delivery.

        The canonical argument body is validated against the pinned tool schema
        and immediately reduced to body-free hashes/bindings.
        """

        self._require_mutable(code="BAD_TOOL_EVENT")
        if self._provider_final_frozen or self._cancelling or self._session_blocked:
            raise NexusTaskLedgerError(
                "a tool was delivered after turn execution froze", code="BAD_TOOL_EVENT"
            )
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TOOL_EVENT"
        )
        name = _text(tool, "tool", maximum=64, code="BAD_TOOL_EVENT")
        if _TOOL_RE.fullmatch(name) is None or name not in self._task_tool_roles:
            raise NexusTaskLedgerError(
                "tool is outside the Nexus contract", code="BAD_TOOL_EVENT"
            )
        request = self._requests.get(corr)
        if request is None or corr != self._latest_corr_id:
            raise NexusTaskLedgerError(
                "a delivered tool is not bound to the latest model request",
                code="BAD_TOOL_EVENT",
            )
        if request.response_outcome or corr in self._tools:
            raise NexusTaskLedgerError(
                "a model request acquired more than one response outcome",
                code="BAD_TOOL_EVENT",
            )
        canonical = _text(
            arguments_canonical,
            "canonical tool arguments",
            maximum=65536,
            empty=False,
            code="BAD_TOOL_EVENT",
        )
        arguments = _json_object(canonical, code="BAD_TOOL_EVENT")
        if canonical_json_bytes(arguments).decode("utf-8") != canonical:
            raise NexusTaskLedgerError(
                "tool arguments are not in canonical JSON form",
                code="BAD_TOOL_EVENT",
            )
        computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if arguments_sha256 is None:
            argument_digest = computed
        else:
            argument_digest = _digest(
                arguments_sha256, "arguments_sha256", code="BAD_TOOL_EVENT"
            )
            if not hmac.compare_digest(argument_digest, computed):
                raise NexusTaskLedgerError(
                    "canonical tool arguments do not match their digest",
                    code="BAD_TOOL_EVENT",
                )
        binding = _tool_argument_binding(name, arguments)
        record = _ToolRecord(corr, name, argument_digest, binding)
        self._tools[corr] = record
        request.response_outcome = "tool"

    def record_workspace_request(
        self,
        corr_id: object,
        *,
        task_id: object,
        tool: object,
        operation: object,
        attempt: object,
        workspace_generation: object,
        arguments_sha256: object,
        objects_sha256: object,
        manifest_cursor: object,
    ) -> str:
        """Bind one body-free Guest workspace exchange to a pending tool.

        The Host validates the request body before calling this method.  The
        ledger retains only the canonical arguments/object roots and the
        workspace generation.  Attempts are correlation-local and strictly
        increasing across manifest pages and the final search/read operation.
        """

        self._require_mutable(code="BAD_WORKSPACE_EVENT")
        if self._provider_final_frozen or self._cancelling or self._session_blocked:
            raise NexusTaskLedgerError(
                "workspace request arrived after turn execution froze",
                code="BAD_WORKSPACE_EVENT",
            )
        corr = _integer(
            corr_id,
            "corr_id",
            minimum=1,
            maximum=MAX_U64,
            code="BAD_WORKSPACE_EVENT",
        )
        record = self._tools.get(corr)
        request = self._requests.get(corr)
        if (
            record is None
            or request is None
            or corr != self._latest_corr_id
            or request.response_outcome != "tool"
            or record.settled
            or record.tool not in WORKSPACE_TOOLS
        ):
            raise NexusTaskLedgerError(
                "workspace request has no active workspace tool",
                code="BAD_WORKSPACE_EVENT",
            )
        name = _text(tool, "tool", maximum=64, code="BAD_WORKSPACE_EVENT")
        op = _text(
            operation, "workspace operation", maximum=16,
            code="BAD_WORKSPACE_EVENT"
        )
        if name != record.tool or op not in WORKSPACE_OPERATIONS[name]:
            raise NexusTaskLedgerError(
                "workspace request changed its delivered tool operation",
                code="BAD_WORKSPACE_EVENT",
            )
        task = _integer(
            task_id,
            "task_id",
            minimum=1,
            maximum=MAX_U32,
            code="BAD_WORKSPACE_EVENT",
        )
        if record.task_id or (
            record.reserved_task_id and record.reserved_task_id != task
        ):
            raise NexusTaskLedgerError(
                "workspace request changed or followed its child Task",
                code="BAD_WORKSPACE_EVENT",
            )
        sequence = _integer(
            attempt,
            "workspace attempt",
            minimum=1,
            maximum=MAX_WORKSPACE_ATTEMPTS,
            code="BAD_WORKSPACE_EVENT",
        )
        if sequence != len(record.workspace_attempts) + 1:
            raise NexusTaskLedgerError(
                "workspace attempts are duplicated or non-consecutive",
                code="BAD_WORKSPACE_EVENT",
            )
        generation = _digest(
            workspace_generation,
            "workspace_generation",
            empty=True,
            code="BAD_WORKSPACE_EVENT",
        )
        arguments = _digest(
            arguments_sha256, "arguments_sha256", code="BAD_WORKSPACE_EVENT"
        )
        objects = _digest(
            objects_sha256, "objects_sha256", code="BAD_WORKSPACE_EVENT"
        )
        cursor = _integer(
            manifest_cursor,
            "manifest_cursor",
            minimum=0,
            maximum=MAX_U32,
            code="BAD_WORKSPACE_EVENT",
        )
        if op != "manifest" and cursor != 0:
            raise NexusTaskLedgerError(
                "non-manifest workspace request exposed a cursor",
                code="BAD_WORKSPACE_EVENT",
            )

        direct_operation = name in DEVELOPMENT_TOOLS
        if record.workspace_attempts:
            previous = record.workspace_attempts[-1]
            if not previous.status:
                raise NexusTaskLedgerError(
                    "workspace request preceded its prior Host result",
                    code="BAD_WORKSPACE_EVENT",
                )
            if previous.status == "error" or (
                previous.status == "ok" and previous.operation == "read"
            ):
                raise NexusTaskLedgerError(
                    "workspace request followed a terminal Host result",
                    code="BAD_WORKSPACE_EVENT",
                )
            if (
                previous.status == "ok"
                and previous.operation == "search"
                and (op != "manifest" or generation != previous.result_generation)
            ):
                raise NexusTaskLedgerError(
                    "paged workspace search did not continue with its manifest",
                    code="BAD_WORKSPACE_EVENT",
                )
            if previous.status == "stale" and (op != "manifest" or generation):
                raise NexusTaskLedgerError(
                    "stale workspace state was not restarted from a manifest",
                    code="BAD_WORKSPACE_EVENT",
                )
            if previous.status == "stale" and cursor != 0:
                raise NexusTaskLedgerError(
                    "stale workspace state did not restart at cursor zero",
                    code="BAD_WORKSPACE_EVENT",
                )
            if (
                previous.status == "ok"
                and op == "manifest"
                and generation != previous.result_generation
            ):
                raise NexusTaskLedgerError(
                    "workspace manifest paging changed its generation",
                    code="BAD_WORKSPACE_EVENT",
                )
            if op == "manifest" and previous.status == "ok":
                manifests = [
                    item
                    for item in record.workspace_attempts
                    if item.operation == "manifest" and item.status == "ok"
                ]
                if not manifests:
                    raise NexusTaskLedgerError(
                        "workspace manifest cursor lacks a prior page",
                        code="BAD_WORKSPACE_EVENT",
                    )
                prior_manifest = manifests[-1]
                if prior_manifest.manifest_eof:
                    raise NexusTaskLedgerError(
                        "workspace manifest continued after end of catalog",
                        code="BAD_WORKSPACE_EVENT",
                    )
                if cursor != prior_manifest.manifest_next_cursor:
                    raise NexusTaskLedgerError(
                        "workspace manifest cursor skipped or repeated a page",
                        code="BAD_WORKSPACE_EVENT",
                    )
        elif direct_operation:
            if generation or cursor != 0:
                raise NexusTaskLedgerError(
                    "development exchange did not start with explicit arguments",
                    code="BAD_WORKSPACE_EVENT",
                )
        elif op != "manifest" or generation or cursor != 0:
            raise NexusTaskLedgerError(
                "workspace exchange did not start with a fresh manifest",
                code="BAD_WORKSPACE_EVENT",
            )

        if op != "manifest" and not direct_operation:
            manifests = [
                item for item in record.workspace_attempts
                if item.operation == "manifest" and item.status == "ok"
            ]
            if (
                not manifests
                or not generation
                or manifests[-1].result_generation != generation
            ):
                raise NexusTaskLedgerError(
                    "workspace operation is not bound to the current manifest",
                    code="BAD_WORKSPACE_EVENT",
                )

        request_root = _sha256(
            {
                "version": 1,
                "corr_id": corr,
                "task_id": task,
                "tool": name,
                "operation": op,
                "attempt": sequence,
                "manifest_cursor": cursor,
                "workspace_generation": generation,
                "arguments_sha256": arguments,
                "objects_sha256": objects,
            }
        )
        record.reserved_task_id = task
        record.workspace_attempts.append(
            _WorkspaceAttempt(
                corr_id=corr,
                task_id=task,
                tool=name,
                operation=op,
                attempt=sequence,
                manifest_cursor=cursor,
                request_generation=generation,
                arguments_sha256=arguments,
                request_objects_sha256=objects,
                request_sha256=request_root,
            )
        )
        return request_root

    def record_workspace_result(
        self,
        corr_id: object,
        *,
        task_id: object,
        tool: object,
        operation: object,
        attempt: object,
        workspace_generation: object,
        arguments_sha256: object,
        result_objects_sha256: object,
        status: object,
        content_bytes: object,
        content_sha256: object,
        manifest_cursor: object,
        manifest_next_cursor: object,
        manifest_eof: object,
        content: object | None = None,
    ) -> str:
        """Settle one Host workspace exchange without retaining its content."""

        self._require_mutable(code="BAD_WORKSPACE_EVENT")
        if self._provider_final_frozen or self._cancelling or self._session_blocked:
            raise NexusTaskLedgerError(
                "workspace result arrived after turn execution froze",
                code="BAD_WORKSPACE_EVENT",
            )
        corr = _integer(
            corr_id,
            "corr_id",
            minimum=1,
            maximum=MAX_U64,
            code="BAD_WORKSPACE_EVENT",
        )
        record = self._tools.get(corr)
        request = self._requests.get(corr)
        if (
            record is None
            or request is None
            or corr != self._latest_corr_id
            or request.response_outcome != "tool"
            or record.settled
            or record.tool not in WORKSPACE_TOOLS
            or not record.workspace_attempts
        ):
            raise NexusTaskLedgerError(
                "workspace result has no active Guest request",
                code="BAD_WORKSPACE_EVENT",
            )
        pending = record.workspace_attempts[-1]
        name = _text(tool, "tool", maximum=64, code="BAD_WORKSPACE_EVENT")
        op = _text(
            operation, "workspace operation", maximum=16,
            code="BAD_WORKSPACE_EVENT"
        )
        sequence = _integer(
            attempt,
            "workspace attempt",
            minimum=1,
            maximum=MAX_WORKSPACE_ATTEMPTS,
            code="BAD_WORKSPACE_EVENT",
        )
        task = _integer(
            task_id,
            "task_id",
            minimum=1,
            maximum=MAX_U32,
            code="BAD_WORKSPACE_EVENT",
        )
        arguments = _digest(
            arguments_sha256, "arguments_sha256", code="BAD_WORKSPACE_EVENT"
        )
        result_objects = _digest(
            result_objects_sha256,
            "result_objects_sha256",
            code="BAD_WORKSPACE_EVENT",
        )
        cursor = _integer(
            manifest_cursor,
            "manifest_cursor",
            minimum=0,
            maximum=MAX_U32,
            code="BAD_WORKSPACE_EVENT",
        )
        next_cursor = _integer(
            manifest_next_cursor,
            "manifest_next_cursor",
            minimum=0,
            maximum=MAX_U32,
            code="BAD_WORKSPACE_EVENT",
        )
        if not isinstance(manifest_eof, bool):
            raise NexusTaskLedgerError(
                "manifest_eof must be boolean", code="BAD_WORKSPACE_EVENT"
            )
        eof = manifest_eof
        if (
            pending.status
            or (task, name, op, sequence, arguments, cursor)
            != (
                pending.task_id,
                pending.tool,
                pending.operation,
                pending.attempt,
                pending.arguments_sha256,
                pending.manifest_cursor,
            )
        ):
            raise NexusTaskLedgerError(
                "workspace result changed, duplicated, or reordered its request",
                code="BAD_WORKSPACE_EVENT",
            )
        outcome = _text(
            status, "workspace status", maximum=16, code="BAD_WORKSPACE_EVENT"
        )
        if outcome not in WORKSPACE_RESULT_STATUSES:
            raise NexusTaskLedgerError(
                "workspace result status is unsupported",
                code="BAD_WORKSPACE_EVENT",
            )
        if op != "manifest" and (cursor != 0 or next_cursor != 0 or eof):
            raise NexusTaskLedgerError(
                "non-manifest workspace result exposed catalog paging",
                code="BAD_WORKSPACE_EVENT",
            )
        if op == "manifest" and outcome != "ok" and (next_cursor != 0 or eof):
            raise NexusTaskLedgerError(
                "unsuccessful manifest exposed catalog paging",
                code="BAD_WORKSPACE_EVENT",
            )
        if op == "manifest" and outcome == "ok":
            if next_cursor < cursor or (not eof and next_cursor == cursor):
                raise NexusTaskLedgerError(
                    "successful manifest cursor did not advance toward EOF",
                    code="BAD_WORKSPACE_EVENT",
                )
        generation = _digest(
            workspace_generation,
            "workspace_generation",
            code="BAD_WORKSPACE_EVENT",
        )
        content_limit = (
            MAX_WORKSPACE_MANIFEST_BYTES
            if op == "manifest"
            else MAX_WORKSPACE_CONTENT_BYTES
        )
        size = _integer(
            content_bytes,
            "content_bytes",
            minimum=0,
            maximum=content_limit,
            code="BAD_WORKSPACE_EVENT",
        )
        digest = _digest(
            content_sha256, "content_sha256", code="BAD_WORKSPACE_EVENT"
        )
        if content is not None:
            if not isinstance(content, str) or "\0" in content:
                raise NexusTaskLedgerError(
                    "workspace content is not valid text",
                    code="BAD_WORKSPACE_EVENT",
                )
            try:
                raw_content = content.encode("utf-8")
            except UnicodeEncodeError as error:
                raise NexusTaskLedgerError(
                    "workspace content is not UTF-8",
                    code="BAD_WORKSPACE_EVENT",
                ) from error
            if (
                len(raw_content) != size
                or len(raw_content) > content_limit
                or not hmac.compare_digest(
                    hashlib.sha256(raw_content).hexdigest(), digest
                )
            ):
                raise NexusTaskLedgerError(
                    "workspace content does not match its bounded digest",
                    code="BAD_WORKSPACE_EVENT",
                )
        empty_digest = hashlib.sha256(b"").hexdigest()
        if outcome == "stale" and (
            size != 0 or not hmac.compare_digest(digest, empty_digest)
        ):
            raise NexusTaskLedgerError(
                "stale workspace result exposed content",
                code="BAD_WORKSPACE_EVENT",
            )
        if outcome == "ok" and not generation:
            raise NexusTaskLedgerError(
                "successful workspace result lacks a generation",
                code="BAD_WORKSPACE_EVENT",
            )
        if (
            outcome == "ok"
            and pending.request_generation
            and generation != pending.request_generation
        ):
            raise NexusTaskLedgerError(
                "workspace content came from a different generation",
                code="BAD_WORKSPACE_EVENT",
            )
        result_root = _sha256(
            {
                "version": 1,
                "request_sha256": pending.request_sha256,
                "manifest_cursor": cursor,
                "manifest_next_cursor": next_cursor,
                "manifest_eof": eof,
                "workspace_generation": generation,
                "objects_sha256": result_objects,
                "status": outcome,
                "content_bytes": size,
                "content_sha256": digest,
            }
        )
        pending.status = outcome
        pending.manifest_next_cursor = next_cursor
        pending.manifest_eof = eof
        pending.result_generation = generation
        pending.result_objects_sha256 = result_objects
        pending.content_bytes = size
        pending.content_sha256 = digest
        pending.result_sha256 = result_root
        if name in DEVELOPMENT_TOOLS and outcome == "ok":
            if content is None:
                raise NexusTaskLedgerError(
                    "development result lacks its bounded projection",
                    code="BAD_WORKSPACE_EVENT",
                )
            self._record_development_result(name, generation, content)
        return result_root

    def _record_development_result(
        self, tool: str, source_revision: str, content: str
    ) -> None:
        """Advance the fail-closed development-evidence state."""

        if content.startswith("development_error\n"):
            self._development_required = True
            return
        if tool in ("write_file", "apply_patch"):
            if _projection_field(content, "revision") != source_revision:
                raise NexusTaskLedgerError(
                    "workspace mutation revision is not self-consistent",
                    code="BAD_WORKSPACE_EVENT",
                )
            self._development_required = True
            self._development_source_revision = source_revision
            self._development_build_id = ""
            self._development_case_kinds.clear()
            return
        if tool == "build_ucore_program":
            self._development_required = True
            if _projection_field(content, "status") == "failed":
                self._development_build_id = ""
                self._development_case_kinds.clear()
                return
            build_id = _projection_field(content, "build_id")
            projected_revision = _projection_field(content, "source_revision")
            if (
                _projection_field(content, "status") != "passed"
                or _DIGEST_RE.fullmatch(build_id) is None
                or projected_revision != source_revision
            ):
                raise NexusTaskLedgerError(
                    "successful build result lacks its source/build binding",
                    code="BAD_WORKSPACE_EVENT",
                )
            self._development_source_revision = source_revision
            self._development_build_id = build_id
            self._development_case_kinds.clear()
            return
        self._development_required = True
        if _projection_field(content, "status") == "failed":
            return
        build_id = _projection_field(content, "build_id")
        projected_revision = _projection_field(content, "source_revision")
        case_kind = _projection_field(content, "case_kind")
        if (
            _projection_field(content, "status") != "passed"
            or build_id != self._development_build_id
            or projected_revision != self._development_source_revision
            or case_kind not in ("normal", "invalid", "failure")
        ):
            raise NexusTaskLedgerError(
                "successful run is not bound to the current build",
                code="BAD_WORKSPACE_EVENT",
            )
        self._development_case_kinds.add(case_kind)

    def workspace_source_sha256(self, corr_id: object) -> str:
        """Return the Guest-compatible source root for accepted workspace pages."""

        self._require_active(code="BAD_WORKSPACE_EVENT")
        corr = _integer(
            corr_id,
            "corr_id",
            minimum=1,
            maximum=MAX_U64,
            code="BAD_WORKSPACE_EVENT",
        )
        record = self._tools.get(corr)
        if (
            record is None
            or record.tool not in WORKSPACE_TOOLS
            or not record.workspace_attempts
            or any(not attempt.status for attempt in record.workspace_attempts)
        ):
            raise NexusTaskLedgerError(
                "workspace source root is not complete",
                code="BAD_WORKSPACE_EVENT",
            )
        accepted: list[_WorkspaceAttempt] = []
        for attempt in record.workspace_attempts:
            if attempt.status == "stale":
                accepted.clear()
            elif attempt.status == "ok":
                accepted.append(attempt)
        if not accepted:
            raise NexusTaskLedgerError(
                "workspace source root has no accepted generation",
                code="BAD_WORKSPACE_EVENT",
            )
        source = bytearray()
        for attempt in accepted:
            request_generation = attempt.request_generation or ("0" * 64)
            source.extend(
                (
                    "workspace_source_attempt_v1\n"
                    f"operation={attempt.operation}\n"
                    f"attempt={attempt.attempt}\n"
                    f"request_generation={request_generation}\n"
                    f"result_generation={attempt.result_generation}\n"
                    f"arguments_sha256={attempt.arguments_sha256}\n"
                    f"objects_sha256={attempt.result_objects_sha256}\n"
                    "status=ok\n"
                    f"content_bytes={attempt.content_bytes}\n"
                    f"content_sha256={attempt.content_sha256}\n"
                ).encode("ascii")
            )
        return hashlib.sha256(source).hexdigest()

    def record_model_request(self, corr_id: object) -> None:
        """Bind the next request after the exact three-event root prelude."""

        self._require_mutable(code="BAD_CORRELATION")
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_CORRELATION"
        )
        if self._cancelling:
            reserved = self._requests.get(corr)
            if (
                corr == self._latest_corr_id
                and reserved is not None
                and not reserved.observed
                and reserved.response_outcome == "cancelled"
                and self._termination_cause == "user_interrupt"
            ):
                root = self._root()
                if (
                    root is None
                    or root.state != "running"
                    or root.progress_count != 1
                    or len(root.events) != 3
                    or root.terminal_event
                ):
                    raise NexusTaskLedgerError(
                        "MODEL_REQUEST lacks the exact root Task prelude",
                        code="BAD_CORRELATION",
                    )
                reserved.observed = True
                return
            if (
                self._termination_cause != "user_interrupt"
                or self._cancel_corr_advanced
                or corr <= self._latest_corr_id
            ):
                raise NexusTaskLedgerError(
                    "a model request arrived after turn execution froze",
                    code="BAD_CORRELATION",
                )
            previous = self._requests.get(self._latest_corr_id)
            if (
                previous is None
                or previous.response_outcome not in ("tool", "retryable_error")
                or (previous.response_outcome == "tool" and not previous.tool_settled)
            ):
                raise NexusTaskLedgerError(
                    "cancel correlation cannot advance past unfinished work",
                    code="BAD_CORRELATION",
                )
            previous.termination_cause = ""
            request = _RequestRecord(
                corr, response_outcome="cancelled",
                termination_cause="user_interrupt"
            )
            self._requests[corr] = request
            self._latest_corr_id = corr
            self._cancel_corr_id = corr
            self._cancel_corr_advanced = True
            return
        if self._provider_final_frozen or self._session_blocked:
            raise NexusTaskLedgerError(
                "a model request arrived after turn execution froze",
                code="BAD_CORRELATION",
            )
        root = self._root()
        if (
            root is None
            or root.state != "running"
            or root.progress_count != 1
            or len(root.events) != 3
            or root.terminal_event
        ):
            raise NexusTaskLedgerError(
                "MODEL_REQUEST lacks the exact root Task prelude",
                code="BAD_CORRELATION",
            )
        if corr in self._requests:
            raise NexusTaskLedgerError(
                "a model request correlation was duplicated", code="BAD_CORRELATION"
            )
        if not self._requests and corr != root.assigned_corr_id:
            raise NexusTaskLedgerError(
                "first MODEL_REQUEST does not match the root prelude correlation",
                code="BAD_CORRELATION",
            )
        if self._latest_corr_id and corr <= self._latest_corr_id:
            raise NexusTaskLedgerError(
                "model request correlations must increase", code="BAD_CORRELATION"
            )
        if self._latest_corr_id:
            previous = self._requests[self._latest_corr_id]
            if not self._request_closed(previous, allow_turn_terminal=False):
                raise NexusTaskLedgerError(
                    "previous model request lacks exactly one settled outcome",
                    code="BAD_CORRELATION",
                )
        self._requests[corr] = _RequestRecord(corr)
        self._latest_corr_id = corr

    def record_model_error(self, corr_id: object, *, retryable: object) -> None:
        """Close one request with a delivered MODEL_ERROR.

        Fatal and last-round errors are additionally armed with
        ``begin_termination``; an ordinary retryable error permits the next
        request but is still a committed per-correlation outcome.
        """

        self._require_mutable(code="BAD_CORRELATION")
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64,
            code="BAD_CORRELATION"
        )
        if not isinstance(retryable, bool):
            raise NexusTaskLedgerError(
                "retryable must be boolean", code="BAD_CORRELATION"
            )
        request = self._latest_request(corr, code="BAD_CORRELATION")
        if request.response_outcome:
            raise NexusTaskLedgerError(
                "a model request acquired more than one response outcome",
                code="BAD_CORRELATION",
            )
        request.response_outcome = "retryable_error" if retryable else "fatal_error"

    def begin_cancel(self, corr_id: object) -> None:
        """Freeze new work while retaining Tasks until cancellation settles."""

        self._require_mutable(code="BAD_TURN")
        if self._provider_final_frozen or self._cancelling:
            raise NexusTaskLedgerError("turn cancellation is not available", code="BAD_TURN")
        corr = _integer(corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TURN")
        if not self._requests:
            root = self._root()
            if (
                root is None
                or corr != root.assigned_corr_id
                or root.terminal_event
            ):
                raise NexusTaskLedgerError(
                    "pre-request cancellation lacks the reserved root correlation",
                    code="BAD_TURN",
                )
            self._requests[corr] = _RequestRecord(
                corr,
                observed=False,
                response_outcome="cancelled",
                termination_cause="user_interrupt",
            )
            self._latest_corr_id = corr
            self._cancelling = True
            self._cancel_corr_id = corr
            self._termination_cause = "user_interrupt"
            return
        self._arm_termination(corr, "user_interrupt")

    def begin_termination(self, corr_id: object, cause: object) -> None:
        """Authorize one bounded, non-success root terminal cause."""

        self._require_mutable(code="BAD_TURN")
        if self._provider_final_frozen or self._cancelling:
            raise NexusTaskLedgerError("turn termination is not available", code="BAD_TURN")
        name = _text(cause, "termination cause", maximum=32, code="BAD_TURN")
        if name not in TERMINATION_CONTRACTS:
            raise NexusTaskLedgerError("termination cause is unsupported", code="BAD_TURN")
        corr = _integer(corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TURN")
        self._arm_termination(corr, name)

    def settle_cancelled_tool_from_task(self, corr_id: object) -> None:
        """Close a cancelled delivered tool when CANCEL suppresses TOOL_EVENT.

        This derives only a negative settlement from an authenticated child
        ``cancelled`` TASK_EVENT.  It never invents artifact, evidence, result,
        or projection metadata.
        """

        self._require_mutable(code="BAD_TOOL_EVENT")
        if not self._cancelling or self._termination_cause not in (
            "user_interrupt",
            "round_limit",
        ):
            raise NexusTaskLedgerError(
                "Task-derived tool cancellation lacks an owned cancel cause",
                code="BAD_TOOL_EVENT",
            )
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TOOL_EVENT"
        )
        record = self._tools.get(corr)
        task = self._tasks.get(record.task_id) if record is not None else None
        if (
            record is None
            or record.settled
            or task is None
            or task.terminal_event != "cancelled"
            or task.terminal_status != AGENT_STATUS_CANCELLED
            or task.artifact_sha256
        ):
            raise NexusTaskLedgerError(
                "cancelled tool lacks its authenticated child Task terminal",
                code="BAD_TOOL_EVENT",
            )
        record.settled = True
        record.status = AGENT_STATUS_CANCELLED
        self._requests[corr].tool_settled = True

    def settle_cancelled_cleanup_tool_at_turn_complete(
        self, corr_id: object, *, turn_status: object
    ) -> bool:
        """Close an exact cancel-derived cleanup trace that emitted no TOOL_EVENT.

        A cleanup-failure root may legally precede a real negative TOOL_EVENT, so
        this settlement is deliberately unavailable until the Guest reaches
        ``TURN_COMPLETE error``.  The staged root marker and authenticated
        cancelled child independently prove the no-TOOL variant.
        """

        self._require_mutable(code="BAD_TURN")
        if _text(turn_status, "turn status", maximum=16, code="BAD_TURN") != "error":
            raise NexusTaskLedgerError(
                "cancel-derived cleanup requires TURN_COMPLETE error", code="BAD_TURN"
            )
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TURN"
        )
        pending_corr = self._pending_cancel_derived_cleanup_corr
        if pending_corr == 0:
            return False
        if corr != pending_corr or self._pending_session_block_corr != corr:
            raise NexusTaskLedgerError(
                "cancel-derived cleanup correlation changed", code="BAD_TURN"
            )
        record = self._tools.get(corr)
        request = self._requests.get(corr)
        task = self._tasks.get(record.task_id) if record is not None else None
        root = self._root()
        if (
            not self._cancelling
            or self._termination_cause != "session_error"
            or not self._session_blocked
            or record is None
            or record.settled
            or request is None
            or request.response_outcome != "tool"
            or request.tool_settled
            or request.termination_cause != "session_error"
            or task is None
            or task.terminal_event != "cancelled"
            or task.terminal_status != AGENT_STATUS_CANCELLED
            or task.artifact_sha256
            or root is None
            or root.terminal_event != "failed"
            or root.terminal_status != AGENT_STATUS_IO_ERROR
            or root.terminal_corr_id != corr
            or not self._children_complete()
        ):
            raise NexusTaskLedgerError(
                "cancel-derived cleanup proof is incomplete", code="BAD_TURN"
            )
        record.settled = True
        record.status = AGENT_STATUS_CANCELLED
        record.session_blocked = True
        request.tool_settled = True
        self._pending_session_block_corr = 0
        self._pending_cancel_derived_cleanup_corr = 0
        return True

    def freeze_provider_final(self, corr_id: object) -> None:
        """Freeze new Task/artifact work after a final response is delivered."""

        self._require_mutable(code="BAD_TURN")
        if self._provider_final_frozen or self._cancelling or self._session_blocked:
            raise NexusTaskLedgerError("provider final cannot freeze this turn", code="BAD_TURN")
        corr = _integer(corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TURN")
        request = self._latest_request(corr, code="BAD_TURN")
        if request.response_outcome:
            raise NexusTaskLedgerError(
                "a model request acquired more than one response outcome",
                code="BAD_TURN",
            )
        if self._pending_artifact_task_id:
            raise NexusTaskLedgerError(
                "provider final preceded an adjacent Task artifact", code="BAD_TURN"
            )
        if any(not tool.settled for tool in self._tools.values()):
            raise NexusTaskLedgerError(
                "provider final preceded tool settlement", code="BAD_TURN"
            )
        root = self._root()
        if root is None or root.state not in ("running", "waiting") or root.progress_count == 0:
            raise NexusTaskLedgerError(
                "provider final lacks the pre-model root Task prelude", code="BAD_TURN"
            )
        if root.terminal_event:
            raise NexusTaskLedgerError(
                "provider final followed a root terminal event", code="BAD_TURN"
            )
        if not self._children_complete():
            raise NexusTaskLedgerError(
                "provider final preceded child Task completion", code="BAD_TURN"
            )
        if self._development_required and (
            not self._development_build_id
            or self._development_case_kinds != {"normal", "invalid", "failure"}
        ):
            raise NexusTaskLedgerError(
                "development final lacks a successful current build and three Guest cases",
                code="BAD_TURN",
            )
        request.response_outcome = "final"
        self._provider_final_frozen = True
        self._final_corr_id = corr

    def record_event(self, event: Mapping[str, object]) -> None:
        """Validate and append one normalized TASK_EVENT projection."""

        self._require_mutable(code="BAD_TASK_EVENT")
        if not isinstance(event, Mapping):
            raise NexusTaskLedgerError("TASK_EVENT must be an object")
        missing = _EVENT_REQUIRED_FIELDS.difference(event)
        unknown = set(event).difference(_EVENT_REQUIRED_FIELDS | _EVENT_OPTIONAL_FIELDS)
        if missing or unknown:
            raise NexusTaskLedgerError("TASK_EVENT fields are malformed")
        if event.get("type") not in (None, "task_event"):
            raise NexusTaskLedgerError("TASK_EVENT type alias is malformed")

        normalized = self._normalize_event(event)
        task_id = int(normalized["task_id"])
        kind = str(normalized["event"])

        if self._pending_artifact_task_id and not (
            task_id == self._pending_artifact_task_id and kind == "artifact_published"
        ):
            raise NexusTaskLedgerError(
                "a successful Task artifact was not the adjacent TASK_EVENT"
            )

        if self._provider_final_frozen:
            root = self._root()
            if (
                root is None
                or task_id != root.task_id
                or (
                    kind != "completed"
                    and not self._is_artifact_cleanup_root(normalized)
                    and not self._is_context_final_failed_root(normalized)
                )
                or root.terminal_event
                or int(normalized["corr_id"]) != self._final_corr_id
            ):
                raise NexusTaskLedgerError(
                    "TASK_EVENT arrived after provider final freeze"
                )
        elif self._cancelling and kind == "assigned":
            # CANCEL and Guest->Host TASK_EVENT frames travel in opposite
            # directions.  A child assignment already authorized by the
            # delivered tool may therefore arrive after the local cancel call.
            pending = self._tools.get(int(normalized["corr_id"]))
            if (
                int(normalized["corr_id"]) != self._cancel_corr_id
                or pending is None
                or pending.settled
                or pending.task_id
            ):
                raise NexusTaskLedgerError("new Task work arrived after cancellation")

        task = self._tasks.get(task_id)
        if task is None:
            if kind != "assigned":
                raise NexusTaskLedgerError("TASK_EVENT references an unknown Task")
            self._record_assignment(normalized)
            return
        if kind == "assigned":
            raise NexusTaskLedgerError("Task assignment was duplicated")
        self._record_transition(task, normalized)

    def settle_tool(
        self,
        corr_id: object,
        *,
        tool: object | None = None,
        status: object,
        value0: object = 0,
        value1: object = 0,
        value2: object = 0,
        provenance: object = 0,
        projection_sha256: object = "",
        workspace_source_sha256: object = "",
        context_seq: object = 0,
        result_sha256: object,
        session_blocked_marker: object = "",
    ) -> None:
        """Bind a TOOL_EVENT settlement to its Task and artifact trace."""

        self._require_mutable(code="BAD_TOOL_EVENT")
        if self._provider_final_frozen:
            raise NexusTaskLedgerError(
                "tool settlement arrived after provider final", code="BAD_TOOL_EVENT"
            )
        corr = _integer(
            corr_id, "corr_id", minimum=1, maximum=MAX_U64, code="BAD_TOOL_EVENT"
        )
        record = self._tools.get(corr)
        if record is None:
            raise NexusTaskLedgerError(
                "tool settlement has an unknown correlation", code="BAD_TOOL_EVENT"
            )
        if record.settled:
            raise NexusTaskLedgerError(
                "tool settlement was duplicated", code="BAD_TOOL_EVENT"
            )
        if tool is not None and tool != record.tool:
            raise NexusTaskLedgerError(
                "tool settlement changed the delivered tool", code="BAD_TOOL_EVENT"
            )
        settled_status = _integer(
            status, "status", minimum=MIN_I32, maximum=MAX_I32, code="BAD_TOOL_EVENT"
        )
        values = tuple(
            _integer(value, label, minimum=0, maximum=MAX_U64, code="BAD_TOOL_EVENT")
            for value, label in ((value0, "value0"), (value1, "value1"), (value2, "value2"))
        )
        labels = _integer(
            provenance,
            "provenance",
            minimum=0,
            maximum=NEXUS_PROVENANCE_ALL,
            code="BAD_TOOL_EVENT",
        )
        projection = _digest(
            projection_sha256,
            "projection_sha256",
            empty=True,
            code="BAD_TOOL_EVENT",
        )
        workspace_source = _digest(
            workspace_source_sha256,
            "workspace_source_sha256",
            empty=True,
            code="BAD_TOOL_EVENT",
        )
        context = _integer(
            context_seq,
            "context_seq",
            minimum=0,
            maximum=MAX_U64,
            code="BAD_TOOL_EVENT",
        )
        result_digest = _digest(result_sha256, "result_sha256", code="BAD_TOOL_EVENT")
        block_marker = _text(
            session_blocked_marker,
            "session_blocked_marker",
            maximum=95,
            empty=True,
            code="BAD_TOOL_EVENT",
        )
        if block_marker and block_marker != _ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT:
            raise NexusTaskLedgerError(
                "tool settlement has an unsupported session-block marker",
                code="BAD_TOOL_EVENT",
            )

        pending_session_block = self._pending_session_block_corr == corr
        if pending_session_block:
            if (
                block_marker != _ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT
                or settled_status != AGENT_STATUS_IO_ERROR
                or any(values)
                or labels != NEXUS_PROVENANCE_FAILURE
                or projection
            ):
                raise NexusTaskLedgerError(
                    "artifact cleanup session block settlement is malformed",
                    code="BAD_TOOL_EVENT",
                )
        elif block_marker:
            raise NexusTaskLedgerError(
                "session-block marker lacks its staged root failure",
                code="BAD_TOOL_EVENT",
            )

        if record.tool in WORKSPACE_TOOLS and settled_status == AGENT_STATUS_OK:
            expected_workspace_source = self.workspace_source_sha256(corr)
            if not workspace_source or not hmac.compare_digest(
                workspace_source, expected_workspace_source
            ):
                raise NexusTaskLedgerError(
                    "workspace tool changed its accepted source root",
                    code="BAD_TOOL_EVENT",
                )
        elif workspace_source:
            raise NexusTaskLedgerError(
                "non-workspace or failed tool exposed a workspace source root",
                code="BAD_TOOL_EVENT",
            )

        expected_role = self._task_tool_roles.get(record.tool)
        task = self._tasks.get(record.task_id) if record.task_id else None
        if pending_session_block:
            if expected_role is None or (
                record.task_id != 0
                and not self._task_execution_complete_for_cleanup(task)
            ):
                raise NexusTaskLedgerError(
                    "artifact cleanup failure lacks completed tool execution",
                    code="BAD_TOOL_EVENT",
                )
        elif expected_role is not None:
            self._bind_task_tool_settlement(
                record,
                task,
                settled_status,
                values,
                labels,
                projection,
                context,
            )
        else:
            raise NexusTaskLedgerError(
                "delivered Nexus tool has no child role", code="BAD_TOOL_EVENT"
            )
        record.settled = True
        record.status = settled_status
        record.value0, record.value1, record.value2 = values
        record.provenance = labels
        record.projection_sha256 = projection
        record.result_sha256 = result_digest
        record.context_seq = context
        record.workspace_source_sha256 = workspace_source
        record.session_blocked = pending_session_block
        request = self._requests[corr]
        request.tool_settled = True
        if pending_session_block:
            self._pending_session_block_corr = 0
            self._pending_cancel_derived_cleanup_corr = 0

    def assert_turn_complete(self, status: object = "completed") -> NexusTaskLedgerSnapshot:
        """Validate the final turn state, seal both roots, and return a snapshot."""

        self._require_active(code="BAD_TURN")
        if self._sealed:
            raise NexusTaskLedgerError("Nexus turn was already sealed", code="BAD_TURN")
        turn_status = _text(status, "turn status", maximum=16, code="BAD_TURN")
        if turn_status not in ("completed", "cancelled", "error"):
            raise NexusTaskLedgerError("turn status is unsupported", code="BAD_TURN")
        if self._pending_artifact_task_id:
            raise NexusTaskLedgerError(
                "turn completion preceded an adjacent Task artifact", code="BAD_TURN"
            )
        if not self._all_required_terminal():
            raise NexusTaskLedgerError(
                "required Task/tool/identity proof is incomplete", code="BAD_TURN"
            )
        root = self._root()
        assert root is not None
        if turn_status == "completed":
            if (
                self._termination_cause
                or self._session_blocked
                or not self._provider_final_frozen
                or root.terminal_event != "completed"
                or root.terminal_status != AGENT_STATUS_OK
            ):
                raise NexusTaskLedgerError(
                    "completed turn lacks its frozen final root result", code="BAD_TURN"
                )
        else:
            contract = TERMINATION_CONTRACTS.get(self._termination_cause)
            if (
                contract is None
                or turn_status != contract[2]
                or (root.terminal_event, root.terminal_status) != contract[:2]
            ):
                raise NexusTaskLedgerError(
                    "TURN_COMPLETE does not match its termination cause",
                    code="BAD_TURN",
                )
        if any(
            not self._request_closed(request, allow_turn_terminal=True)
            for request in self._requests.values()
        ):
            raise NexusTaskLedgerError(
                "a model request lacks exactly one outcome", code="BAD_TURN"
            )
        self._task_root_sha256 = _sha256(
            {
                "version": 4,
                "tasks": self._task_root_items(),
                "tools": self._tool_root_items(),
            }
        )
        self._artifact_root_sha256 = _sha256(
            {
                "version": 3,
                "task_artifacts": self._artifact_root_items(),
            }
        )
        self._sealed = True
        return self.snapshot()

    def snapshot(self) -> NexusTaskLedgerSnapshot:
        """Return an immutable snapshot without summary or argument bodies."""

        tasks = tuple(self._task_snapshot(task) for task in self._ordered_tasks())
        lifecycle = self._turn_lifecycle or (0, 0)
        return NexusTaskLedgerSnapshot(
            active=self.active,
            sealed=self._sealed,
            turn_id=self._turn_id,
            request_id=self._request_id,
            workflow_lifecycle_id=lifecycle[0],
            workflow_lifecycle_generation=lifecycle[1],
            provider_final_frozen=self._provider_final_frozen,
            cancelling=self._cancelling,
            termination_cause=self._termination_cause,
            session_blocked=self._session_blocked,
            latest_corr_id=self._latest_corr_id,
            model_request_count=len(self._requests),
            task_count=len(self._tasks),
            event_count=self._event_count,
            delivered_tool_count=len(self._tools),
            settled_tool_count=sum(record.settled for record in self._tools.values()),
            all_required_terminal=self._all_required_terminal(),
            task_root_sha256=self._task_root_sha256,
            artifact_root_sha256=self._artifact_root_sha256,
            tasks=tasks,
        )

    def clear(self, *, reset_session: bool = False) -> None:
        """Clear per-turn proof state; optionally clear session bindings too."""

        if not isinstance(reset_session, bool):
            raise NexusTaskLedgerError(
                "reset_session must be boolean", code="BAD_LEDGER_CONFIG"
            )
        self._clear_turn()
        if reset_session:
            self._session_lifecycle = None
            self._kernel_identities.clear()
            self._role_identities.clear()
            self._session_blocked = False

    def set_kernel_identity(
        self,
        *,
        role: object,
        pid: object,
        agent_id: object,
        control_id: object,
    ) -> None:
        """Install one Host-verified identity and reconcile existing Tasks."""

        identity = KernelIdentity(
            _text(role, "role", maximum=24),
            _integer(pid, "pid", minimum=1, maximum=MAX_I32),
            _integer(agent_id, "agent_id", minimum=1, maximum=MAX_I32),
            _integer(control_id, "control_id", minimum=1, maximum=FULL_U64_MAX),
        )
        if identity.role not in BUSINESS_ROLES:
            raise NexusTaskLedgerError("kernel identity has an unsupported role")
        self._install_kernel_identity(identity)

    def _clear_turn(self) -> None:
        self._turn_id = 0
        self._request_id = 0
        self._turn_lifecycle: tuple[int, int] | None = None
        self._tasks: dict[int, _TaskRecord] = {}
        self._tools: dict[int, _ToolRecord] = {}
        self._requests: dict[int, _RequestRecord] = {}
        self._latest_corr_id = 0
        self._root_task_id = 0
        self._event_count = 0
        self._pending_artifact_task_id = 0
        self._provider_final_frozen = False
        self._final_corr_id = 0
        self._cancelling = False
        self._cancel_corr_id = 0
        self._termination_cause = ""
        self._cancel_corr_advanced = False
        self._pending_session_block_corr = 0
        self._pending_cancel_derived_cleanup_corr = 0
        self._sealed = False
        self._task_root_sha256 = ""
        self._artifact_root_sha256 = ""
        self._development_required = False
        self._development_source_revision = ""
        self._development_build_id = ""
        self._development_case_kinds: set[str] = set()

    def _require_active(self, *, code: str) -> None:
        if not self.active:
            raise NexusTaskLedgerError("there is no active Nexus turn", code=code)

    def _require_mutable(self, *, code: str) -> None:
        self._require_active(code=code)
        if self._sealed:
            raise NexusTaskLedgerError("Nexus proof is already sealed", code=code)

    def _bind_lifecycle(self, lifecycle: tuple[int, int], *, code: str) -> None:
        if self._session_lifecycle is None:
            self._session_lifecycle = lifecycle
        elif lifecycle != self._session_lifecycle:
            raise NexusTaskLedgerError(
                "workflow lifecycle changed within the session", code=code
            )

    def _latest_request(self, corr_id: int, *, code: str) -> _RequestRecord:
        request = self._requests.get(corr_id)
        if request is None or corr_id != self._latest_corr_id:
            raise NexusTaskLedgerError(
                "correlation is not the latest active model request", code=code
            )
        return request

    @staticmethod
    def _request_closed(
        request: _RequestRecord, *, allow_turn_terminal: bool
    ) -> bool:
        if not request.observed:
            return False
        if request.response_outcome == "tool":
            return request.tool_settled
        if request.response_outcome == "retryable_error":
            return not request.termination_cause or allow_turn_terminal
        if request.response_outcome in ("final", "fatal_error", "cancelled"):
            return allow_turn_terminal
        return False

    def _arm_termination(self, corr_id: int, cause: str) -> None:
        request = self._latest_request(corr_id, code="BAD_TURN")
        if request.termination_cause or self._cancelling:
            raise NexusTaskLedgerError(
                "turn termination was duplicated", code="BAD_TURN"
            )
        if cause == "session_error":
            if not self._session_blocked or request.response_outcome != "tool":
                raise NexusTaskLedgerError(
                    "session_error lacks an indeterminate child Task",
                    code="BAD_TURN",
                )
        elif cause == "provider_fatal":
            if request.response_outcome != "fatal_error":
                raise NexusTaskLedgerError(
                    "provider fatal conflicts with the request outcome",
                    code="BAD_TURN",
                )
        elif cause == "round_limit":
            if request.response_outcome not in ("tool", "retryable_error"):
                raise NexusTaskLedgerError(
                    "round limit conflicts with the request outcome",
                    code="BAD_TURN",
                )
        elif cause == "user_interrupt":
            if request.response_outcome == "":
                request.response_outcome = "cancelled"
            elif request.response_outcome not in ("tool", "retryable_error"):
                raise NexusTaskLedgerError(
                    "user interrupt conflicts with the request outcome",
                    code="BAD_TURN",
                )
        elif cause == "context_final_failed":
            if (
                not self._provider_final_frozen
                or corr_id != self._final_corr_id
                or request.response_outcome != "final"
                or self._session_blocked
            ):
                raise NexusTaskLedgerError(
                    "context final failure lacks its frozen provider final",
                    code="BAD_TURN",
                )
        request.termination_cause = cause
        self._cancelling = True
        self._cancel_corr_id = corr_id
        self._termination_cause = cause

    def _normalize_event(self, source: Mapping[str, object]) -> dict[str, object]:
        turn = _integer(source["turn_id"], "turn_id", minimum=1, maximum=MAX_U64)
        request = _integer(
            source["request_id"], "request_id", minimum=1, maximum=MAX_U64
        )
        if turn != self._turn_id or request != self._request_id:
            raise NexusTaskLedgerError("TASK_EVENT changed its active turn/request")
        lifecycle = (
            _integer(
                source["workflow_lifecycle_id"],
                "workflow_lifecycle_id",
                minimum=1,
                maximum=MAX_U64,
            ),
            _integer(
                source["workflow_lifecycle_generation"],
                "workflow_lifecycle_generation",
                minimum=1,
                maximum=MAX_U64,
            ),
        )
        self._bind_lifecycle(lifecycle, code="BAD_TASK_EVENT")
        if self._turn_lifecycle is None:
            self._turn_lifecycle = lifecycle
        elif lifecycle != self._turn_lifecycle:
            raise NexusTaskLedgerError("TASK_EVENT changed its turn lifecycle")

        kind = _text(source["event"], "event", maximum=32)
        state = _text(source["task_state"], "task_state", maximum=16)
        role = _text(source["role"], "role", maximum=24)
        if kind not in TASK_EVENTS or state not in TASK_STATES or role not in BUSINESS_ROLES:
            raise NexusTaskLedgerError("TASK_EVENT enum is unsupported")
        expected_states = {
            "assigned": frozenset(("assigned",)),
            "accepted": frozenset(("accepted",)),
            "progress": frozenset(("running", "waiting")),
            "completed": frozenset(("completed",)),
            "failed": frozenset(("failed",)),
            "cancelled": frozenset(("cancelled",)),
            "artifact_published": frozenset(("completed",)),
        }
        if state not in expected_states[kind]:
            raise NexusTaskLedgerError("TASK_EVENT event/state pair is invalid")
        if "agent_role" in source and source["agent_role"] != role:
            raise NexusTaskLedgerError("TASK_EVENT changed its role alias")

        known = source["control_id_known"]
        if not isinstance(known, bool):
            raise NexusTaskLedgerError("control_id_known must be boolean")
        control_value = source.get("control_id")
        if known:
            control = _integer(
                control_value, "control_id", minimum=1, maximum=FULL_U64_MAX
            )
            if "agent_control_id" in source and source["agent_control_id"] != control:
                raise NexusTaskLedgerError("TASK_EVENT changed its control identity alias")
        else:
            if control_value is not None or "agent_control_id" in source:
                raise NexusTaskLedgerError("unknown control identity was exposed")
            control = 0

        raw_digest = source.get("digest", "")
        alias_digest = source.get("artifact_sha256", raw_digest)
        if raw_digest and alias_digest != raw_digest:
            raise NexusTaskLedgerError("TASK_EVENT changed its artifact digest alias")
        artifact_digest = _digest(
            alias_digest, "artifact_sha256", empty=True
        )
        summary = source.get("summary", "")
        summary_text = _text(
            summary, "summary", maximum=256, empty=True
        )
        summary_raw = summary_text.encode("utf-8")

        normalized: dict[str, object] = {
            "turn_id": turn,
            "request_id": request,
            "corr_id": _integer(
                source["corr_id"], "corr_id", minimum=1, maximum=MAX_U64
            ),
            "workflow_lifecycle_id": lifecycle[0],
            "workflow_lifecycle_generation": lifecycle[1],
            "task_id": _integer(
                source["task_id"], "task_id", minimum=1, maximum=MAX_U32
            ),
            "parent_task_id": _integer(
                source["parent_task_id"],
                "parent_task_id",
                minimum=0,
                maximum=MAX_U32,
            ),
            "event": kind,
            "task_state": state,
            "role": role,
            "agent_pid": _integer(
                source["agent_pid"], "agent_pid", minimum=1, maximum=MAX_I32
            ),
            "agent_id": _integer(
                source["agent_id"], "agent_id", minimum=1, maximum=MAX_I32
            ),
            "control_id_known": known,
            "control_id": control,
            "status": _integer(
                source["status"], "status", minimum=MIN_I32, maximum=MAX_I32
            ),
            "tick": _integer(source["tick"], "tick", minimum=0, maximum=MAX_U64),
            "deadline_tick": _integer(
                source.get("deadline_tick", 0),
                "deadline_tick",
                minimum=0,
                maximum=MAX_U32,
            ),
            "artifact_handle": _integer(
                source.get("artifact_handle", 0),
                "artifact_handle",
                minimum=0,
                maximum=MAX_U32,
            ),
            "context_seq": _integer(
                source.get("context_seq", 0),
                "context_seq",
                minimum=0,
                maximum=MAX_U64,
            ),
            "provenance": _integer(
                source.get("provenance", 0),
                "provenance",
                minimum=0,
                maximum=NEXUS_PROVENANCE_ALL,
            ),
            "metric_code": _integer(
                source.get("metric_code", 0),
                "metric_code",
                minimum=0,
                maximum=MAX_U32,
            ),
            "metric_value": _integer(
                source.get("metric_value", 0),
                "metric_value",
                minimum=0,
                maximum=MAX_U32,
            ),
            "resource_used": _integer(
                source.get("resource_used", 0),
                "resource_used",
                minimum=0,
                maximum=MAX_U64,
            ),
            "source_pid": _integer(
                source.get("source_pid", 0),
                "source_pid",
                minimum=1,
                maximum=MAX_I32,
            ),
            "target_pid": _integer(
                source.get("target_pid", 0),
                "target_pid",
                minimum=1,
                maximum=MAX_I32,
            ),
            "artifact_sha256": artifact_digest,
            "summary_bytes": len(summary_raw),
            "summary_sha256": (
                hashlib.sha256(summary_raw).hexdigest() if summary_raw else ""
            ),
        }
        task_channel_binding = (0, 0, 0, 0)
        if normalized["parent_task_id"] != 0:
            if kind == "assigned":
                task_channel_binding = _task_channel_binding(
                    summary_text, phase="assigned"
                )
            elif kind in ("accepted", "progress"):
                raise NexusTaskLedgerError(
                    "native Task Channel child emitted a legacy transition"
                )
            elif kind in TASK_TERMINALS and not (
                kind == "failed"
                and normalized["status"] == AGENT_STATUS_INDETERMINATE
                and summary_text == _SESSION_BLOCK_SUMMARY
            ):
                task_channel_binding = _task_channel_binding(
                    summary_text, phase="cqe"
                )
                if normalized["context_seq"] == 0:
                    raise NexusTaskLedgerError(
                        "Task Channel terminal lacks its CQE context sequence"
                    )
            if normalized["metric_code"] or normalized["metric_value"]:
                raise NexusTaskLedgerError(
                    "native Task Channel child exposed legacy progress metrics"
                )
        normalized["task_channel_binding"] = task_channel_binding
        cleanup_marker = bool(
            normalized["summary_bytes"]
            == len(_ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT.encode("utf-8"))
            and normalized["summary_sha256"]
            == _ARTIFACT_CLEANUP_SESSION_BLOCK_SHA256
        )
        if cleanup_marker and not (
            normalized["parent_task_id"] == 0
            and self._is_artifact_cleanup_root(normalized)
        ):
            raise NexusTaskLedgerError(
                "artifact cleanup marker has an invalid root terminal envelope"
            )
        context_final_failed_marker = bool(
            normalized["summary_bytes"]
            == len(_CONTEXT_FINAL_FAILED_SUMMARY.encode("utf-8"))
            and normalized["summary_sha256"] == _CONTEXT_FINAL_FAILED_SHA256
        )
        if context_final_failed_marker and not (
            normalized["parent_task_id"] == 0
            and self._is_context_final_failed_root(normalized)
        ):
            raise NexusTaskLedgerError(
                "context final failure marker has an invalid root terminal envelope"
            )
        if (normalized["metric_code"] == 0) != (normalized["metric_value"] == 0):
            # metric_value may legitimately be zero, but the wire omits both
            # fields unless metric_code is nonzero.  Presence resolves that case.
            if not (
                "metric_code" in source
                and "metric_value" in source
                and normalized["metric_code"] != 0
            ):
                raise NexusTaskLedgerError("TASK_EVENT metric fields are incomplete")
        return normalized

    def _record_assignment(self, value: Mapping[str, object]) -> None:
        if len(self._tasks) >= self.max_tasks:
            raise NexusTaskLedgerError("Nexus Task bound was exceeded")
        task_id = int(value["task_id"])
        parent_id = int(value["parent_task_id"])
        corr_id = int(value["corr_id"])
        role = str(value["role"])
        deadline = int(value["deadline_tick"])
        status = int(value["status"])
        if status != AGENT_STATUS_OK:
            raise NexusTaskLedgerError("Task assignment has nonzero status")
        if parent_id == 0:
            expected_root = NEXUS_ROOT_TASK_BASE + self._turn_id
            if self._root_task_id or task_id != expected_root or role != "coordinator":
                raise NexusTaskLedgerError("root Task identity is malformed")
            if deadline != 0:
                raise NexusTaskLedgerError("root Task acquired a deadline")
            if (
                int(value["source_pid"]) != int(value["agent_pid"])
                or int(value["target_pid"]) != int(value["agent_pid"])
            ):
                raise NexusTaskLedgerError("root Task routing is malformed")
            self._root_task_id = task_id
        else:
            root = self._root()
            if root is None or parent_id != root.task_id:
                raise NexusTaskLedgerError("child Task references an unknown parent")
            if root.state not in ("running", "waiting") or root.progress_count == 0:
                raise NexusTaskLedgerError("child Task preceded the root prelude")
            if root.terminal_event:
                raise NexusTaskLedgerError("child Task followed its parent terminal")
            tool = self._tools.get(corr_id)
            if tool is None or tool.settled:
                raise NexusTaskLedgerError("child Task has no delivered pending tool")
            if corr_id != self._latest_corr_id or corr_id not in self._requests:
                raise NexusTaskLedgerError("child Task uses a stale model correlation")
            expected_role = self._task_tool_roles.get(tool.tool)
            if (
                expected_role is None
                or expected_role != role
                or tool.task_id
                or (tool.reserved_task_id and tool.reserved_task_id != task_id)
            ):
                raise NexusTaskLedgerError("child Task/tool role binding is malformed")
            if tool.tool in WORKSPACE_TOOLS and not self._workspace_ready(tool):
                raise NexusTaskLedgerError(
                    "workspace child Task preceded its accepted Host content"
                )
            if deadline == 0 or deadline <= int(value["tick"]):
                raise NexusTaskLedgerError("child Task deadline is not in the future")
            if (
                int(value["source_pid"]) != root.agent_pid
                or int(value["target_pid"]) != int(value["agent_pid"])
            ):
                raise NexusTaskLedgerError("child Task assignment routing is malformed")
            tool.task_id = task_id
        channel_binding = value["task_channel_binding"]
        assert isinstance(channel_binding, tuple) and len(channel_binding) == 4
        task = _TaskRecord(
            turn_id=self._turn_id,
            request_id=self._request_id,
            lifecycle_id=int(value["workflow_lifecycle_id"]),
            lifecycle_generation=int(value["workflow_lifecycle_generation"]),
            task_id=task_id,
            parent_task_id=parent_id,
            assigned_corr_id=corr_id,
            role=role,
            agent_pid=int(value["agent_pid"]),
            agent_id=int(value["agent_id"]),
            control_id_known=bool(value["control_id_known"]),
            control_id=int(value["control_id"]),
            deadline_tick=deadline,
            channel_generation=int(channel_binding[0]),
            channel_request_id=int(channel_binding[1]),
            slot_generation=int(channel_binding[2]),
            contract_generation=int(channel_binding[3]),
        )
        task.identity_verified = self._verify_task_identity(task)
        self._tasks[task_id] = task
        self._append_event(task, value)

    def _record_transition(
        self, task: _TaskRecord, value: Mapping[str, object]
    ) -> None:
        self._check_stable_task_fields(task, value)
        kind = str(value["event"])
        status = int(value["status"])
        corr_id = int(value["corr_id"])
        root = self._root()
        if task.parent_task_id == 0:
            if (
                int(value["source_pid"]) != task.agent_pid
                or int(value["target_pid"]) != task.agent_pid
            ):
                raise NexusTaskLedgerError("root Task routing is malformed")
        else:
            assert root is not None
            synthetic_block = (
                kind == "failed"
                and status == AGENT_STATUS_TASK_FAILED
                and value["summary_sha256"] == _SESSION_BLOCK_SUMMARY_SHA256
            )
            if kind in TASK_TERMINALS and not synthetic_block:
                channel_binding = value["task_channel_binding"]
                expected_channel_binding = (
                    task.channel_generation,
                    task.channel_request_id,
                    task.slot_generation,
                    task.contract_generation,
                )
                if channel_binding != expected_channel_binding:
                    raise NexusTaskLedgerError(
                        "Task Channel CQE changed its submitted binding"
                    )
            expected_route = (root.agent_pid, task.agent_pid)
            if (int(value["source_pid"]), int(value["target_pid"])) != expected_route:
                raise NexusTaskLedgerError("child Task event routing is malformed")
            if status == AGENT_STATUS_TASK_FAILED and not synthetic_block:
                raise NexusTaskLedgerError(
                    "indeterminate Task lacks the session-block marker"
                )

        if task.parent_task_id:
            if corr_id != task.assigned_corr_id:
                raise NexusTaskLedgerError("child Task changed its tool correlation")
        elif kind not in TASK_TERMINALS and corr_id != task.assigned_corr_id:
            raise NexusTaskLedgerError("root Task prelude changed its correlation")

        if task.terminal_event:
            if kind != "artifact_published" or task.artifact_sha256:
                raise NexusTaskLedgerError("Task emitted an event after its terminal result")
            if task.terminal_event != "completed":
                raise NexusTaskLedgerError("unsuccessful Task published an artifact")
            self._record_artifact(task, value)
            return

        if kind == "accepted":
            if task.state != "assigned" or status != AGENT_STATUS_OK:
                raise NexusTaskLedgerError("Task acceptance jumped or was duplicated")
            task.state = "accepted"
        elif kind == "progress":
            if status != AGENT_STATUS_OK:
                raise NexusTaskLedgerError("Task progress jumped or followed a terminal")
            if task.parent_task_id == 0:
                if (
                    task.state != "accepted"
                    or task.progress_count != 0
                    or value["task_state"] != "running"
                ):
                    raise NexusTaskLedgerError(
                        "root Task prelude must contain one running progress event"
                    )
            elif task.state not in ("accepted", "running", "waiting"):
                raise NexusTaskLedgerError("Task progress jumped or followed a terminal")
            task.state = str(value["task_state"])
            task.progress_count += 1
        elif kind in TASK_TERMINALS:
            if task.parent_task_id == 0:
                allowed_states = ("running",)
            elif kind == "completed":
                allowed_states = ("assigned",)
            else:
                allowed_states = ("assigned",)
            if task.state not in allowed_states:
                raise NexusTaskLedgerError("Task terminal result followed an invalid state")
            if kind == "completed" and status != AGENT_STATUS_OK:
                raise NexusTaskLedgerError("completed Task has nonzero status")
            if kind == "failed" and status >= AGENT_STATUS_OK:
                raise NexusTaskLedgerError("failed Task has a non-negative status")
            if kind == "cancelled" and status != AGENT_STATUS_CANCELLED:
                raise NexusTaskLedgerError("cancelled Task has the wrong status")
            if task.parent_task_id == 0:
                if task.progress_count != 1 or len(task.events) != 3:
                    raise NexusTaskLedgerError("root Task terminal lacks its exact prelude")
                request = self._requests.get(corr_id)
                if request is None or not request.observed:
                    raise NexusTaskLedgerError(
                        "root Task terminal lacks its observed model request"
                    )
                if kind == "completed":
                    if not self._provider_final_frozen or corr_id != self._final_corr_id:
                        raise NexusTaskLedgerError(
                            "root completion is not bound to the frozen provider final"
                        )
                else:
                    self._stage_context_final_failure(corr_id, value)
                    self._stage_artifact_cleanup_session_error(
                        corr_id, value
                    )
                    contract = TERMINATION_CONTRACTS.get(self._termination_cause)
                    if (
                        not self._cancelling
                        or corr_id != self._cancel_corr_id
                        or contract is None
                        or (kind, status) != contract[:2]
                    ):
                        raise NexusTaskLedgerError(
                            "root result does not match its explicit termination cause"
                        )
                unsettled = [
                    tool for tool in self._tools.values() if not tool.settled
                ]
                deferred_terminal_tool = (
                    self._termination_cause in ("round_limit", "session_error")
                    and len(unsettled) == 1
                    and unsettled[0].corr_id == self._cancel_corr_id
                    and (
                        unsettled[0].task_id == 0
                        or (
                            self._tasks[unsettled[0].task_id].terminal_event != ""
                            and (
                                self._tasks[unsettled[0].task_id].terminal_event
                                != "completed"
                                or bool(self._tasks[unsettled[0].task_id].artifact_sha256)
                            )
                        )
                    )
                )
                if not self._children_complete() or (
                    unsettled and not deferred_terminal_tool
                ):
                    raise NexusTaskLedgerError("root terminated before its child/tool work")
            task.state = kind
            task.terminal_event = kind
            task.terminal_corr_id = corr_id
            task.terminal_status = status
            task.terminal_context_seq = int(value["context_seq"])
            if task.parent_task_id and kind == "completed":
                self._pending_artifact_task_id = task.task_id
            if task.parent_task_id and status == AGENT_STATUS_TASK_FAILED:
                self._session_blocked = True
                if self._cancelling:
                    if self._termination_cause not in (
                        "user_interrupt", "round_limit", "session_error"
                    ):
                        raise NexusTaskLedgerError(
                            "indeterminate Task conflicts with turn termination"
                        )
                    self._termination_cause = "session_error"
                    self._requests[corr_id].termination_cause = "session_error"
                else:
                    self._arm_termination(corr_id, "session_error")
        elif kind == "artifact_published":
            raise NexusTaskLedgerError("Task published an artifact before completion")
        else:
            raise NexusTaskLedgerError("Task transition is unsupported")
        self._append_event(task, value)

    def _stage_artifact_cleanup_session_error(
        self,
        corr_id: int,
        value: Mapping[str, object],
    ) -> None:
        """Stage the root-first half of an exact cleanup-failure handshake."""

        if not self._is_artifact_cleanup_root(value):
            return
        pending = [tool for tool in self._tools.values() if not tool.settled]
        request = self._requests.get(corr_id)
        tool = pending[0] if len(pending) == 1 else None
        task = (
            self._tasks.get(tool.task_id)
            if tool is not None and tool.task_id != 0
            else None
        )
        execution_complete = bool(
            tool is not None
            and (
                tool.task_id == 0
                or self._task_execution_complete_for_cleanup(task)
            )
        )
        pending_tool_cleanup = bool(
            tool is not None
            and tool.corr_id == corr_id
            and tool.tool in self._task_tool_roles
            and execution_complete
            and corr_id == self._latest_corr_id
            and request is not None
            and request.response_outcome == "tool"
            and not self._provider_final_frozen
            and self._termination_cause
            in ("", "user_interrupt", "round_limit", "session_error")
            and self._children_complete()
        )
        if pending_tool_cleanup:
            self._pending_session_block_corr = corr_id
            self._pending_cancel_derived_cleanup_corr = (
                corr_id
                if self._termination_cause == "user_interrupt"
                and task is not None
                and task.terminal_event == "cancelled"
                and task.terminal_status == AGENT_STATUS_CANCELLED
                and not task.artifact_sha256
                else 0
            )
        elif pending:
            raise NexusTaskLedgerError(
                "artifact cleanup root preceded complete tool execution"
            )
        elif not self._terminal_cleanup_outcome_is_owned(corr_id, request):
            raise NexusTaskLedgerError(
                "artifact cleanup root lacks an owned terminal outcome"
            )
        elif request is not None and request.response_outcome == "tool":
            settled_tool = self._tools.get(corr_id)
            if settled_tool is None or not settled_tool.settled:
                raise NexusTaskLedgerError(
                    "artifact cleanup root lacks its settled tool outcome"
                )
            settled_tool.session_blocked = True
        self._session_blocked = True
        self._cancelling = True
        self._cancel_corr_id = corr_id
        self._termination_cause = "session_error"
        assert request is not None
        request.termination_cause = "session_error"

    def _stage_context_final_failure(
        self,
        corr_id: int,
        value: Mapping[str, object],
    ) -> None:
        """Bind the exact post-provider Context FINAL append failure."""

        if not self._is_context_final_failed_root(value):
            return
        self._arm_termination(corr_id, "context_final_failed")

    @staticmethod
    def _task_execution_complete_for_cleanup(
        task: _TaskRecord | None,
    ) -> bool:
        if task is None:
            return False
        if task.terminal_event == "completed":
            return bool(task.artifact_sha256)
        return task.terminal_event in ("failed", "cancelled")

    @staticmethod
    def _is_artifact_cleanup_root(value: Mapping[str, object]) -> bool:
        return bool(
            value["event"] == "failed"
            and value["status"] == AGENT_STATUS_IO_ERROR
            and value["summary_bytes"]
            == len(_ARTIFACT_CLEANUP_SESSION_BLOCK_RESULT.encode("utf-8"))
            and value["summary_sha256"]
            == _ARTIFACT_CLEANUP_SESSION_BLOCK_SHA256
            and value["deadline_tick"] == 0
            and value["artifact_handle"] == 0
            and value["artifact_sha256"] == ""
            and value["resource_used"] == 0
            and value["provenance"] == NEXUS_PROVENANCE_FAILURE
            and value["metric_code"] == 0
            and value["metric_value"] == 0
        )

    @staticmethod
    def _is_context_final_failed_root(value: Mapping[str, object]) -> bool:
        return bool(
            value["event"] == "failed"
            and value["status"] == AGENT_STATUS_NO_SPACE
            and value["summary_bytes"]
            == len(_CONTEXT_FINAL_FAILED_SUMMARY.encode("utf-8"))
            and value["summary_sha256"] == _CONTEXT_FINAL_FAILED_SHA256
            and value["deadline_tick"] == 0
            and value["artifact_handle"] == 0
            and value["artifact_sha256"] == ""
            and value["resource_used"] == 0
            and value["provenance"] == NEXUS_PROVENANCE_FAILURE
            and value["metric_code"] == 0
            and value["metric_value"] == 0
        )

    def _terminal_cleanup_outcome_is_owned(
        self,
        corr_id: int,
        request: _RequestRecord | None,
    ) -> bool:
        if (
            request is None
            or not request.observed
            or corr_id != self._latest_corr_id
            or not self._children_complete()
            or any(not tool.settled for tool in self._tools.values())
        ):
            return False
        if request.response_outcome == "final":
            return bool(
                self._provider_final_frozen
                and corr_id == self._final_corr_id
                and not self._termination_cause
            )
        if request.response_outcome == "fatal_error":
            return bool(
                self._cancelling
                and corr_id == self._cancel_corr_id
                and self._termination_cause == "provider_fatal"
            )
        if request.response_outcome == "cancelled":
            return bool(
                self._cancelling
                and corr_id == self._cancel_corr_id
                and self._termination_cause == "user_interrupt"
            )
        if request.response_outcome == "retryable_error":
            return bool(
                self._cancelling
                and corr_id == self._cancel_corr_id
                and self._termination_cause == "round_limit"
            )
        if request.response_outcome == "tool":
            return bool(
                request.tool_settled
                and self._cancelling
                and corr_id == self._cancel_corr_id
                and self._termination_cause
                in ("user_interrupt", "round_limit", "session_error")
            )
        return False

    def _record_artifact(
        self, task: _TaskRecord, value: Mapping[str, object]
    ) -> None:
        if self._provider_final_frozen:
            raise NexusTaskLedgerError("Task artifact arrived after execution froze")
        if self._pending_artifact_task_id != task.task_id:
            raise NexusTaskLedgerError("Task artifact was not adjacent to completion")
        if int(value["status"]) != AGENT_STATUS_OK or value["task_state"] != "completed":
            raise NexusTaskLedgerError("Task artifact has an invalid completed state")
        digest = str(value["artifact_sha256"])
        provenance = int(value["provenance"])
        resource = int(value["resource_used"])
        if not digest or provenance == 0 or resource == 0:
            raise NexusTaskLedgerError("Task artifact lacks integrity metadata")
        tool = self._tools.get(task.assigned_corr_id)
        assert tool is not None
        expected_provenance = TASK_ARTIFACT_PROVENANCE.get(tool.tool)
        if expected_provenance is None or provenance != expected_provenance:
            raise NexusTaskLedgerError("Task artifact provenance is not tool-canonical")
        handle = int(value["artifact_handle"])
        if handle != 0:
            raise NexusTaskLedgerError("transient Task artifact exposed a handle")
        context = int(value["context_seq"])
        if context == 0:
            raise NexusTaskLedgerError(
                "Coordinator artifact acceptance lacks its Context sequence"
            )
        if task.terminal_context_seq == 0 or context <= task.terminal_context_seq:
            raise NexusTaskLedgerError(
                "Coordinator artifact acceptance did not follow its Task Channel CQE"
            )
        workspace = self._workspace_content(tool)
        if tool.tool == "read_file" or tool.tool in DEVELOPMENT_TOOLS:
            if workspace is None or (
                resource != workspace.content_bytes
                or not hmac.compare_digest(digest, workspace.content_sha256)
            ):
                raise NexusTaskLedgerError(
                    "workspace Task artifact does not bind accepted Host content"
                )
        task.artifact_handle = handle
        task.artifact_sha256 = digest
        task.artifact_context_seq = context
        task.resource_used = resource
        task.provenance = provenance
        self._append_event(task, value)
        self._pending_artifact_task_id = 0

    @staticmethod
    def _workspace_content(tool: _ToolRecord) -> _WorkspaceAttempt | None:
        targets = {
            "search_files": "search",
            "read_file": "read",
            "write_file": "write",
            "apply_patch": "patch",
            "build_ucore_program": "build",
            "run_ucore_program": "run",
        }
        target = targets.get(tool.tool, "")
        for attempt in reversed(tool.workspace_attempts):
            if attempt.status == "stale":
                break
            if attempt.operation == target and attempt.status == "ok":
                return attempt
        return None

    @staticmethod
    def _workspace_ready(tool: _ToolRecord) -> bool:
        if not tool.workspace_attempts or not tool.workspace_attempts[-1].status:
            return False
        if tool.workspace_attempts[-1].status != "ok":
            return False
        if tool.tool in DEVELOPMENT_TOOLS:
            return NexusTaskLedger._workspace_content(tool) is not None
        if tool.tool == "read_file":
            return NexusTaskLedger._workspace_content(tool) is not None
        final = tool.workspace_attempts[-1]
        if final.operation == "manifest":
            return final.manifest_eof
        for attempt in reversed(tool.workspace_attempts):
            if attempt.status == "stale":
                break
            if attempt.operation == "manifest" and attempt.status == "ok":
                return True
        return False

    def _append_event(
        self, task: _TaskRecord, value: Mapping[str, object]
    ) -> None:
        if self._event_count >= self.max_events or len(task.events) >= self.max_events_per_task:
            raise NexusTaskLedgerError("Nexus TASK_EVENT bound was exceeded")
        if task.events and int(value["tick"]) < int(task.events[-1]["tick"]):
            raise NexusTaskLedgerError("Task tick moved backwards")
        projected = _fixed(
            _EVENT_HASH_FIELDS,
            {
                "version": 1,
                "corr_id": value["corr_id"],
                "event": value["event"],
                "task_state": value["task_state"],
                "status": value["status"],
                "tick": value["tick"],
                "deadline_tick": value["deadline_tick"],
                "context_seq": value["context_seq"],
                "metric_code": value["metric_code"],
                "metric_value": value["metric_value"],
                "artifact_handle": value["artifact_handle"],
                "artifact_sha256": value["artifact_sha256"],
                "resource_used": value["resource_used"],
                "provenance": value["provenance"],
                "source_pid": value["source_pid"],
                "target_pid": value["target_pid"],
                "summary_bytes": value["summary_bytes"],
                "summary_sha256": value["summary_sha256"],
            },
        )
        task.events.append(projected)
        self._event_count += 1

    def _check_stable_task_fields(
        self, task: _TaskRecord, value: Mapping[str, object]
    ) -> None:
        actual = (
            value["turn_id"],
            value["request_id"],
            value["workflow_lifecycle_id"],
            value["workflow_lifecycle_generation"],
            value["parent_task_id"],
            value["role"],
            value["agent_pid"],
            value["agent_id"],
            value["control_id_known"],
            value["control_id"],
            value["deadline_tick"],
        )
        expected = (
            task.turn_id,
            task.request_id,
            task.lifecycle_id,
            task.lifecycle_generation,
            task.parent_task_id,
            task.role,
            task.agent_pid,
            task.agent_id,
            task.control_id_known,
            task.control_id,
            task.deadline_tick,
        )
        if actual != expected:
            raise NexusTaskLedgerError("Task identity or envelope changed")
        task.identity_verified = self._verify_task_identity(task)

    def _bind_task_tool_settlement(
        self,
        tool: _ToolRecord,
        task: _TaskRecord | None,
        status: int,
        values: tuple[int, int, int],
        provenance: int,
        projection: str,
        context_seq: int,
    ) -> None:
        if task is None:
            if status == AGENT_STATUS_OK:
                raise NexusTaskLedgerError(
                    "successful task tool has no child Task", code="BAD_TOOL_EVENT"
                )
            if (
                any(values)
                or projection
                or provenance != NEXUS_PROVENANCE_FAILURE
            ):
                raise NexusTaskLedgerError(
                    "pre-dispatch failure exposed Task result bindings",
                    code="BAD_TOOL_EVENT",
                )
            return
        if task.terminal_event == "":
            raise NexusTaskLedgerError(
                "tool settled before its child Task", code="BAD_TOOL_EVENT"
            )
        if task.terminal_event == "completed":
            if status != AGENT_STATUS_OK or not task.artifact_sha256:
                raise NexusTaskLedgerError(
                    "successful child Task/tool result is incomplete",
                    code="BAD_TOOL_EVENT",
                )
            if (
                tool.tool in WORKSPACE_TOOLS
                and values != (task.resource_used, task.task_id, task.agent_id)
            ):
                raise NexusTaskLedgerError(
                    "tool values do not bind the child Task artifact",
                    code="BAD_TOOL_EVENT",
                )
            if (
                task.artifact_context_seq == 0
                or context_seq != task.artifact_context_seq
            ):
                raise NexusTaskLedgerError(
                    "tool Context sequence does not bind Coordinator artifact acceptance",
                    code="BAD_TOOL_EVENT",
                )
            expected_tool_provenance = TOOL_PROVENANCE[tool.tool]
            expected_task_provenance = TASK_ARTIFACT_PROVENANCE[tool.tool]
            if (
                provenance != expected_tool_provenance
                or task.provenance != expected_task_provenance
                or not hmac.compare_digest(
                projection, task.artifact_sha256
                )
            ):
                raise NexusTaskLedgerError(
                    "tool integrity metadata does not bind the Task artifact",
                    code="BAD_TOOL_EVENT",
                )
        else:
            allowed = status == task.terminal_status
            if task.terminal_event == "cancelled":
                allowed = status in (AGENT_STATUS_CANCELLED, AGENT_STATUS_TIMEOUT)
            elif (
                task.terminal_event == "failed"
                and task.terminal_status == AGENT_STATUS_TASK_FAILED
            ):
                allowed = status in (AGENT_STATUS_TASK_FAILED, AGENT_STATUS_IO_ERROR)
            if (
                not allowed
                or any(values)
                or projection
                or provenance != NEXUS_PROVENANCE_FAILURE
            ):
                raise NexusTaskLedgerError(
                    "failed child Task/tool result binding is malformed",
                    code="BAD_TOOL_EVENT",
                )

    def _children_complete(self) -> bool:
        for task in self._tasks.values():
            if task.parent_task_id == 0:
                continue
            if not task.terminal_event:
                return False
            if task.terminal_event == "completed" and not task.artifact_sha256:
                return False
        return self._pending_artifact_task_id == 0

    def _all_required_terminal(self) -> bool:
        if not self.active or self._pending_artifact_task_id:
            return False
        root = self._root()
        if root is None or not root.terminal_event or not self._children_complete():
            return False
        if any(not tool.settled for tool in self._tools.values()):
            return False
        if not self._requests or any(
            not request.observed
            or not self._request_closed(request, allow_turn_terminal=True)
            for request in self._requests.values()
        ):
            return False
        if self._require_kernel_identity and any(
            not task.identity_verified for task in self._tasks.values()
        ):
            return False
        return True

    def _root(self) -> _TaskRecord | None:
        return self._tasks.get(self._root_task_id)

    def _ordered_tasks(self) -> tuple[_TaskRecord, ...]:
        return tuple(self._tasks[task_id] for task_id in sorted(self._tasks))

    def _task_root_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for task in self._ordered_tasks():
            tool = self._tools.get(task.assigned_corr_id) if task.parent_task_id else None
            event_sha256 = _sha256(task.events)
            items.append(
                _fixed(
                    _TASK_ROOT_ITEM_FIELDS,
                    {
                        "version": 1,
                        "turn_id": task.turn_id,
                        "request_id": task.request_id,
                        "workflow_lifecycle_id": task.lifecycle_id,
                        "workflow_lifecycle_generation": task.lifecycle_generation,
                        "task_id": task.task_id,
                        "parent_task_id": task.parent_task_id,
                        "assigned_corr_id": task.assigned_corr_id,
                        "terminal_corr_id": task.terminal_corr_id,
                        "role": task.role,
                        "agent_pid": task.agent_pid,
                        "agent_id": task.agent_id,
                        "control_id_known": task.control_id_known,
                        "control_id": task.control_id,
                        "deadline_tick": task.deadline_tick,
                        "terminal_event": task.terminal_event,
                        "terminal_status": task.terminal_status,
                        "terminal_context_seq": task.terminal_context_seq,
                        "artifact_handle": task.artifact_handle,
                        "artifact_sha256": task.artifact_sha256,
                        "artifact_context_seq": task.artifact_context_seq,
                        "resource_used": task.resource_used,
                        "provenance": task.provenance,
                        "tool": tool.tool if tool is not None else "",
                        "arguments_sha256": tool.arguments_sha256 if tool is not None else "",
                        "projection_sha256": tool.projection_sha256 if tool is not None else "",
                        "result_sha256": tool.result_sha256 if tool is not None else "",
                        "event_count": len(task.events),
                        "event_sha256": event_sha256,
                    },
                )
            )
        return items

    def _artifact_root_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for task in self._ordered_tasks():
            if not task.artifact_sha256:
                continue
            tool = self._tools[task.assigned_corr_id]
            items.append(
                _fixed(
                    _ARTIFACT_ROOT_ITEM_FIELDS,
                    {
                        "version": 1,
                        "turn_id": task.turn_id,
                        "request_id": task.request_id,
                        "workflow_lifecycle_id": task.lifecycle_id,
                        "workflow_lifecycle_generation": task.lifecycle_generation,
                        "task_id": task.task_id,
                        "parent_task_id": task.parent_task_id,
                        "corr_id": task.assigned_corr_id,
                        "role": task.role,
                        "agent_pid": task.agent_pid,
                        "agent_id": task.agent_id,
                        "control_id": task.control_id,
                        "tool": tool.tool,
                        "artifact_handle": task.artifact_handle,
                        "artifact_sha256": task.artifact_sha256,
                        "cqe_context_seq": task.terminal_context_seq,
                        "consumption_context_seq": task.artifact_context_seq,
                        "resource_used": task.resource_used,
                        "provenance": task.provenance,
                        "projection_sha256": tool.projection_sha256,
                    },
                )
            )
        return items

    def _tool_root_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for corr_id in sorted(self._tools):
            tool = self._tools[corr_id]
            items.append(
                _fixed(
                    _TOOL_ROOT_ITEM_FIELDS,
                    {
                        "version": 1,
                        "turn_id": self._turn_id,
                        "request_id": self._request_id,
                        "corr_id": tool.corr_id,
                        "tool": tool.tool,
                        "arguments_sha256": tool.arguments_sha256,
                        "argument_binding_sha256": tool.argument_binding_sha256,
                        "task_id": tool.task_id,
                        "settled": tool.settled,
                        "status": tool.status,
                        "value0": tool.value0,
                        "value1": tool.value1,
                        "value2": tool.value2,
                        "provenance": tool.provenance,
                        "projection_sha256": tool.projection_sha256,
                        "result_sha256": tool.result_sha256,
                        "context_seq": tool.context_seq,
                        "workspace_source_sha256": tool.workspace_source_sha256,
                        "workspace_attempt_count": len(tool.workspace_attempts),
                        "workspace_attempts_sha256": (
                            _sha256(self._workspace_attempt_items(tool))
                            if tool.workspace_attempts
                            else ""
                        ),
                        "session_blocked": tool.session_blocked,
                    },
                )
            )
        return items

    @staticmethod
    def _workspace_attempt_items(
        tool: _ToolRecord,
    ) -> list[dict[str, object]]:
        return [
            _fixed(
                _WORKSPACE_ATTEMPT_FIELDS,
                {
                    "version": 1,
                    "corr_id": attempt.corr_id,
                    "task_id": attempt.task_id,
                    "tool": attempt.tool,
                    "operation": attempt.operation,
                    "attempt": attempt.attempt,
                    "manifest_cursor": attempt.manifest_cursor,
                    "manifest_next_cursor": attempt.manifest_next_cursor,
                    "manifest_eof": attempt.manifest_eof,
                    "request_generation": attempt.request_generation,
                    "result_generation": attempt.result_generation,
                    "arguments_sha256": attempt.arguments_sha256,
                    "request_objects_sha256": attempt.request_objects_sha256,
                    "result_objects_sha256": attempt.result_objects_sha256,
                    "request_sha256": attempt.request_sha256,
                    "status": attempt.status,
                    "content_bytes": attempt.content_bytes,
                    "content_sha256": attempt.content_sha256,
                    "result_sha256": attempt.result_sha256,
                },
            )
            for attempt in tool.workspace_attempts
        ]

    def _task_snapshot(self, task: _TaskRecord) -> NexusTaskSnapshot:
        tool = self._tools.get(task.assigned_corr_id) if task.parent_task_id else None
        return NexusTaskSnapshot(
            task_id=task.task_id,
            parent_task_id=task.parent_task_id,
            assigned_corr_id=task.assigned_corr_id,
            terminal_corr_id=task.terminal_corr_id,
            role=task.role,
            agent_pid=task.agent_pid,
            agent_id=task.agent_id,
            control_id_known=task.control_id_known,
            control_id=task.control_id,
            deadline_tick=task.deadline_tick,
            state=task.state,
            terminal_event=task.terminal_event,
            terminal_status=task.terminal_status,
            terminal_context_seq=task.terminal_context_seq,
            artifact_handle=task.artifact_handle,
            artifact_sha256=task.artifact_sha256,
            artifact_context_seq=task.artifact_context_seq,
            resource_used=task.resource_used,
            provenance=task.provenance,
            tool=tool.tool if tool is not None else "",
            identity_verified=task.identity_verified,
            event_count=len(task.events),
            event_sha256=_sha256(task.events),
        )

    def _verify_task_identity(self, task: _TaskRecord) -> bool:
        identity = self._lookup_kernel_identity(task.agent_pid)
        if identity is None:
            return False
        actual = KernelIdentity(task.role, task.agent_pid, task.agent_id, task.control_id)
        if not task.control_id_known or actual != identity:
            raise NexusTaskLedgerError("TASK_EVENT does not match its kernel identity")
        return True

    def _lookup_kernel_identity(self, pid: int) -> KernelIdentity | None:
        identity = self._kernel_identities.get(pid)
        if identity is not None or self._identity_lookup is None:
            return identity
        try:
            supplied = self._identity_lookup(pid)
        except Exception as error:
            raise NexusTaskLedgerError("kernel identity lookup failed") from error
        if supplied is None:
            return None
        identity = self._coerce_kernel_identity(supplied, expected_pid=pid)
        self._install_kernel_identity(identity)
        return identity

    def _coerce_kernel_identity(
        self, supplied: object, *, expected_pid: int
    ) -> KernelIdentity:
        if isinstance(supplied, KernelIdentity):
            identity = supplied
        elif isinstance(supplied, Mapping):
            identity = KernelIdentity(
                str(supplied.get("role", "")),
                supplied.get("pid", expected_pid),  # type: ignore[arg-type]
                supplied.get("agent_id", 0),  # type: ignore[arg-type]
                supplied.get(
                    "control_id", supplied.get("actor_control_id", 0)
                ),  # type: ignore[arg-type]
            )
        elif isinstance(supplied, (tuple, list)) and len(supplied) == 4:
            identity = KernelIdentity(*supplied)  # type: ignore[arg-type]
        else:
            raise NexusTaskLedgerError("kernel identity lookup returned malformed data")
        checked = KernelIdentity(
            _text(identity.role, "role", maximum=24),
            _integer(identity.pid, "pid", minimum=1, maximum=MAX_I32),
            _integer(identity.agent_id, "agent_id", minimum=1, maximum=MAX_I32),
            _integer(
                identity.control_id,
                "control_id",
                minimum=1,
                maximum=FULL_U64_MAX,
            ),
        )
        if checked.pid != expected_pid or checked.role not in BUSINESS_ROLES:
            raise NexusTaskLedgerError("kernel identity lookup returned the wrong Agent")
        return checked

    def _install_kernel_identity(self, identity: KernelIdentity) -> None:
        previous = self._kernel_identities.get(identity.pid)
        if previous is not None and previous != identity:
            raise NexusTaskLedgerError("kernel identity changed for one PID")
        role_previous = self._role_identities.get(identity.role)
        if role_previous is not None and role_previous != identity:
            raise NexusTaskLedgerError("business role changed kernel identity")
        for other in self._kernel_identities.values():
            if other.role != identity.role and (
                other.agent_id == identity.agent_id
                or other.control_id == identity.control_id
            ):
                raise NexusTaskLedgerError("kernel identity was reused across roles")
        self._kernel_identities[identity.pid] = identity
        self._role_identities[identity.role] = identity
        for task in self._tasks.values():
            if task.agent_pid != identity.pid:
                continue
            actual = KernelIdentity(
                task.role, task.agent_pid, task.agent_id, task.control_id
            )
            if not task.control_id_known or actual != identity:
                raise NexusTaskLedgerError("stored Task conflicts with kernel identity")
            task.identity_verified = True


__all__ = (
    "AGENT_STATUS_CANCELLED",
    "AGENT_STATUS_IO_ERROR",
    "AGENT_STATUS_INDETERMINATE",
    "AGENT_STATUS_NO_SPACE",
    "AGENT_STATUS_OK",
    "AGENT_STATUS_TASK_FAILED",
    "AGENT_STATUS_TIMEOUT",
    "BUSINESS_ROLES",
    "KernelIdentity",
    "MAX_WORKSPACE_ATTEMPTS",
    "MAX_WORKSPACE_CONTENT_BYTES",
    "MAX_WORKSPACE_MANIFEST_BYTES",
    "NEXUS_ROOT_TASK_BASE",
    "NexusTaskLedger",
    "NexusTaskLedgerError",
    "NexusTaskLedgerSnapshot",
    "NexusTaskSnapshot",
    "TASK_EVENTS",
    "TASK_STATES",
    "TASK_TOOL_ROLES",
    "WORKSPACE_OPERATIONS",
    "WORKSPACE_RESULT_STATUSES",
    "WORKSPACE_TOOLS",
    "canonical_json_bytes",
)
