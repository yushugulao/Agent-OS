#!/usr/bin/env python3
"""Validate one task-independent AgentOS Nexus controller/observer replay.

The validator binds provider requests, model replies, child Tasks, tool
settlements, terminal state, and observer-safe projections.  Tool choice,
tool order, task wording, and the semantic content of the final answer remain
model-owned.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Sequence


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_contract as nexus_contract  # noqa: E402
import agentos_nexus_task_ledger as task_ledger  # noqa: E402


ROOT = _HERE.parent
DEFAULT_SCRIPT = ROOT / "ci" / "agentos-nexus-script.txt"
DEFAULT_WORKSPACE = ROOT

MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 65_536
MAX_USER_BYTES = 2048
MAX_FINAL_BYTES = 2048
MAX_ROUNDS = 16
MAX_RETRIES = 32
MAX_PROVIDER_ATTEMPTS = MAX_ROUNDS + MAX_RETRIES
MAX_RAW_TOOL_CALLS = 32
MAX_DEEPSEEK_ADAPTER_ATTEMPTS = 2
MAX_MODEL_ERROR_MESSAGE_BYTES = 240
MAX_HISTORY_BINDINGS = 4
MAX_TOOL_ARGUMENT_STRING_BYTES = 3072
MAX_WORKSPACE_RESULT_BYTES = 2800
MAX_U64 = (1 << 63) - 1
U64_MAX = (1 << 64) - 1
MAX_U32 = (1 << 32) - 1
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
SESSION_RE = re.compile(r"[0-9a-f]{32}\Z")
MODEL_ERROR_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
NEXUS_RETRYABLE_RESPONSE_ERROR_CODES = frozenset(
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

TOOL_NAMES = tuple(str(item["name"]) for item in nexus_contract.TOOLS)
TOOL_SET = frozenset(TOOL_NAMES)
TASK_TOOLS = frozenset(task_ledger.TASK_TOOL_ROLES)
SUCCESS_PROVENANCE = dict(task_ledger.TOOL_PROVENANCE)
BUSINESS_ROLES = frozenset(task_ledger.BUSINESS_ROLES)
CONTROL_ID_FIELDS = frozenset(
    ("control_id", "agent_control_id", "actor_control_id")
)
ARTIFACT_CLEANUP_SESSION_BLOCK = "artifact_cleanup_failed;session_blocked=1"
CONTEXT_FINAL_FAILED = "context_final_failed"
WORKSPACE_OBSERVATION_RESULT = (
    "workspace_observation_ready;agentos_catalog=1;task_channel=1"
)
SYSTEM_OBSERVATION_RESULT = "system_observation_ready;task_channel=1"
FILE_READ_MAX_LINES = 64

MODEL_REQUEST_FIELDS = frozenset(
    (
        "type",
        "turn_id",
        "request_id",
        "corr_id",
        "round",
        "attempt",
        "request_sha256",
        "raw_guest_request_sha256",
        "history_bindings",
        "context_path",
        "request_contains_user",
        "user_message_index",
        "generation",
        "user_content_sha256",
        "user_bytes",
    )
)
MODEL_PROOF_FIELDS = (
    "generation",
    "provider",
    "model",
    "transport",
    "adapter_success",
    "request_sha256",
    "raw_guest_request_sha256",
    "history_bindings",
    "request_contains_user",
    "user_message_index",
    "response_sha256",
    "user_content_sha256",
    "user_bytes",
)
RECEIPT_FIELDS = (
    "provider_endpoint",
    "http_status",
    "provider_request_sha256",
    "provider_response_sha256",
    "selected_reply_sha256",
    "attempt_count",
    "tool_choice_mode",
    "raw_tool_call_count",
    "selected_index",
    "adaptation",
    "forced_tool",
    "selected_tool_sha256",
)
FINAL_PROOF_FIELDS = (
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
TOOL_EVENT_FIELDS = frozenset(
    (
        "type",
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
        "context_seq",
        "provenance",
        "projection_sha256",
        "result_sha256",
        "artifact_sha256",
        "data_trust",
        "workspace_source_sha256",
        "model_projection",
    )
)
WORKSPACE_REQUEST_FIELDS = frozenset(
    (
        "type",
        "turn_id",
        "request_id",
        "corr_id",
        "task_id",
        "tool",
        "operation",
        "attempt",
        "workspace_generation",
        "arguments_sha256",
        "objects_sha256",
        "manifest_cursor",
    )
)
WORKSPACE_RESULT_FIELDS = WORKSPACE_REQUEST_FIELDS | frozenset(
    (
        "status",
        "content_bytes",
        "content_sha256",
        "manifest_next_cursor",
        "manifest_eof",
    )
)
OBSERVER_SAFE_FIELDS = frozenset(
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
OBSERVER_FORBIDDEN_KEYS = frozenset(
    (
        "answer",
        "arguments",
        "canonical_arguments",
        "content",
        "detail",
        "display",
        "message",
        "model_projection",
        "nonce",
        "objective",
        "raw",
        "result",
        "summary",
        "system",
        "tools",
        "user_message",
        "workspace_result",
    )
)


class ValidationError(ValueError):
    """A captured Nexus artifact violates the acceptance contract."""


@dataclass(frozen=True)
class FixtureRecord:
    request_sha256: str
    response: dict[str, object]


@dataclass(frozen=True)
class _ContextTurnBinding:
    turn_id: int
    request_id: int
    user_sequence: int
    final_sequence: int
    sha256: str


@dataclass(frozen=True)
class _ContextPath:
    version: int
    branch_generation: int
    visible_head_sequence: int
    current_user_sequence: int
    turns: tuple[_ContextTurnBinding, ...]


@dataclass(frozen=True)
class _CompletedContextTurn:
    turn_id: int
    request_id: int
    user_sequence: int
    final_sequence: int
    pair_sha256: str | None


@dataclass(frozen=True)
class TurnReport:
    turn_id: int
    request_id: int
    status: str
    request_count: int
    tool_calls: tuple[tuple[int, str], ...]
    direct_final: bool
    cancel_requested: bool
    cancelled_active_worker: bool
    final_content: str


@dataclass(frozen=True)
class ValidationSummary:
    fixture_records: int
    turns: tuple[TurnReport, ...]
    provider: str


def _fail(message: str) -> None:
    raise ValidationError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _is_int(value: object, minimum: int = 0, maximum: int = MAX_U64) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _digest(value: object, label: str, *, empty: bool = False) -> str:
    if empty and value == "":
        return ""
    _require(isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None,
             f"{label} is not a lowercase SHA-256 digest")
    return str(value)


def _context_path(value: object, label: str = "context_path") -> _ContextPath:
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
    _require(
        isinstance(value, dict) and set(value) == root_fields,
        f"{label} fields are malformed",
    )
    assert isinstance(value, dict)
    _require(
        value.get("version") == nexus_contract.CONTEXT_PATH_VERSION
        and not isinstance(value.get("version"), bool),
        f"{label} version is unsupported",
    )
    for field in (
        "branch_generation",
        "visible_head_sequence",
        "current_user_sequence",
    ):
        _require(
            _is_int(value.get(field), 1, U64_MAX),
            f"{label} {field} is not positive u64",
        )
    visible_head_sequence = int(value["visible_head_sequence"])
    current_user_sequence = int(value["current_user_sequence"])
    _require(
        current_user_sequence <= visible_head_sequence,
        f"{label} current user is outside the visible Context path",
    )
    raw_turns = value.get("turns")
    _require(
        isinstance(raw_turns, list)
        and len(raw_turns) <= nexus_contract.CONTEXT_PATH_MAX_TURNS,
        f"{label} turns are not a bounded list",
    )
    assert isinstance(raw_turns, list)
    turns: list[_ContextTurnBinding] = []
    previous_turn_id = 0
    previous_request_id = 0
    previous_final_sequence = 0
    for index, raw_turn in enumerate(raw_turns):
        turn_label = f"{label} turn {index}"
        _require(
            isinstance(raw_turn, dict) and set(raw_turn) == turn_fields,
            f"{turn_label} fields are malformed",
        )
        assert isinstance(raw_turn, dict)
        for field in (
            "turn_id",
            "request_id",
            "user_sequence",
            "final_sequence",
        ):
            _require(
                _is_int(raw_turn.get(field), 1, U64_MAX),
                f"{turn_label} {field} is not positive u64",
            )
        turn = _ContextTurnBinding(
            turn_id=int(raw_turn["turn_id"]),
            request_id=int(raw_turn["request_id"]),
            user_sequence=int(raw_turn["user_sequence"]),
            final_sequence=int(raw_turn["final_sequence"]),
            sha256=_digest(raw_turn.get("sha256"), f"{turn_label} digest"),
        )
        _require(
            turn.turn_id > previous_turn_id
            and turn.request_id > previous_request_id,
            f"{label} turn bindings are not increasing",
        )
        _require(
            turn.user_sequence > previous_final_sequence
            and turn.final_sequence > turn.user_sequence
            and turn.final_sequence < current_user_sequence,
            f"{label} Context sequences are not increasing",
        )
        turns.append(turn)
        previous_turn_id = turn.turn_id
        previous_request_id = turn.request_id
        previous_final_sequence = turn.final_sequence
    return _ContextPath(
        version=int(value["version"]),
        branch_generation=int(value["branch_generation"]),
        visible_head_sequence=visible_head_sequence,
        current_user_sequence=current_user_sequence,
        turns=tuple(turns),
    )


def _context_path_projection(path: _ContextPath) -> dict[str, object]:
    return {
        "version": path.version,
        "branch_generation": path.branch_generation,
        "visible_head_sequence": path.visible_head_sequence,
        "current_user_sequence": path.current_user_sequence,
        "turns": [
            {
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "user_sequence": turn.user_sequence,
                "final_sequence": turn.final_sequence,
                "sha256": turn.sha256,
            }
            for turn in path.turns
        ],
    }


def _pair_sha256(user: str, assistant: str) -> str:
    try:
        raw = user.encode("utf-8") + b"\0" + assistant.encode("utf-8")
    except UnicodeEncodeError:
        _fail("Context pair is not scalar UTF-8")
    return hashlib.sha256(raw).hexdigest()


def _bounded_text(
    value: object, label: str, maximum: int, *, empty: bool = False
) -> str:
    _require(isinstance(value, str), f"{label} must be text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(f"{label} is not scalar UTF-8")
    _require((empty or bool(raw)) and len(raw) <= maximum,
             f"{label} is outside its byte bound")
    _require(not any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value),
             f"{label} contains terminal controls")
    return str(value)


def _bounded_tool_text(
    value: object, label: str, maximum: int, *, empty: bool = False
) -> str:
    _require(
        isinstance(value, str) and (empty or bool(value)) and "\0" not in value,
        f"{label} is not valid bounded tool text",
    )
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(f"{label} is not scalar UTF-8")
    escaped_bytes = sum(
        6 if byte < 0x20 else (2 if byte in (ord('"'), ord("\\")) else 1)
        for byte in raw
    )
    _require(
        len(value) <= maximum,
        f"{label} exceeds its schema maxLength",
    )
    _require(
        len(raw) <= MAX_TOOL_ARGUMENT_STRING_BYTES
        and escaped_bytes <= MAX_TOOL_ARGUMENT_STRING_BYTES,
        f"{label} exceeds its raw or escaped Guest transport budget",
    )
    return str(value)


def _bounded_workspace_result(value: object, label: str) -> str:
    _require(
        isinstance(value, str) and "\0" not in value,
        f"{label} is not valid Host workspace text",
    )
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(f"{label} is not scalar UTF-8")
    _require(
        len(raw) <= MAX_WORKSPACE_RESULT_BYTES,
        f"{label} exceeds its raw UTF-8 byte bound",
    )
    return str(value)


def _bounded_final_text(value: object, label: str, maximum: int) -> str:
    _require(
        isinstance(value, str) and bool(value) and "\0" not in value,
        f"{label} is not valid final text",
    )
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(f"{label} is not scalar UTF-8")
    _require(len(raw) <= maximum, f"{label} exceeds its UTF-8 byte bound")
    return str(value)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _system_projection(
    operation: object, value0: object, value1: object, value2: object
) -> str:
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
            "context_status",
            "record_count",
            "record_capacity",
            "call_count",
        ),
    }
    specification = specifications.get(operation)
    _require(
        specification is not None
        and _is_int(value0, 0, MAX_U64)
        and _is_int(value1, 0, MAX_U64)
        and value2 == 0,
        "system observation values do not match its operation",
    )
    assert specification is not None
    tool, first_label, second_label, omitted = specification
    return (
        "scope=this_boot_guest_runtime\n"
        "content_untrusted=1\n"
        f"operation={operation}\n"
        f"tool={tool}\n"
        "status=0\n"
        f"{first_label}={value0}\n"
        f"{second_label}={value1}\n"
        f"volatile_fields_omitted={omitted}\n"
    )


def _reject_constant(value: str) -> object:
    raise ValidationError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_jsonl(path: Path, label: str) -> list[dict[str, object]]:
    try:
        info = path.lstat()
    except OSError as error:
        raise ValidationError(f"{label} is unavailable: {error}") from error
    _require(stat.S_ISREG(info.st_mode) and not path.is_symlink(),
             f"{label} must be a regular non-symlink file")
    _require(0 < info.st_size <= MAX_TRANSCRIPT_BYTES,
             f"{label} is empty or oversized")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValidationError(f"{label} is not readable UTF-8") from error
    _require(not text.startswith("\ufeff"), f"{label} has a forbidden UTF-8 BOM")
    lines = text.splitlines()
    _require(lines and len(lines) <= MAX_RECORDS, f"{label} record count is invalid")
    _require(all(line.strip() for line in lines), f"{label} contains a blank record")
    records: list[dict[str, object]] = []
    for number, line in enumerate(lines, 1):
        try:
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except ValidationError:
            raise
        except (ValueError, json.JSONDecodeError, RecursionError) as error:
            raise ValidationError(f"{label} line {number} is invalid JSON") from error
        _require(isinstance(value, dict), f"{label} line {number} is not an object")
        records.append(value)
    return records


def _validate_script_text(text: str) -> tuple[str, ...]:
    _require(not text.startswith("\ufeff"), "Nexus script has a UTF-8 BOM")
    lines = [line.rstrip("\r") for line in text.split("\n") if line.rstrip("\r")]
    _require(lines and all(not line.startswith("#") for line in lines),
             "Nexus script comments are executable user turns")
    _require(lines[0] == "/tools" and lines[-1] == "/quit",
             "Nexus script must inspect the tool contract first and close explicitly")
    allowed = {
        "/tools",
        "/status",
        "/context",
        "/tasks",
        "/agents",
        "/artifacts",
        "/quit",
    }
    _require(all(not line.startswith("/") or line in allowed for line in lines),
             "Nexus script contains an unsupported or effectful command")
    commands = [line for line in lines if line.startswith("/")]
    _require(commands.count("/tools") == 1, "Nexus script must inspect its catalog once")
    goals = tuple(line for line in lines if not line.startswith("/"))
    _require(bool(goals), "Nexus replay must submit at least one natural user task")
    for goal in goals:
        _bounded_text(goal, "scripted user goal", MAX_USER_BYTES)
    return goals


def _validate_tool_arguments(tool: str, arguments: object) -> dict[str, object]:
    _require(isinstance(arguments, dict), f"{tool} arguments must be an object")
    keys = set(arguments)
    if tool == "search_files":
        _require({"query"} <= keys <= {"query", "path_prefix"},
                 "search_files arguments are malformed")
        _bounded_tool_text(
            arguments["query"], "search_files.query", 95, empty=True
        )
        if "path_prefix" in arguments:
            _bounded_tool_text(
                arguments["path_prefix"],
                "search_files.path_prefix",
                111,
                empty=True,
            )
    elif tool == "read_file":
        _require(keys == {"path", "start_line", "max_lines"},
                 "read_file arguments are malformed")
        _bounded_tool_text(arguments["path"], "read_file.path", 255)
        _require(_is_int(arguments["start_line"], 1, MAX_U32),
                 "read_file start_line is malformed")
        _require(_is_int(arguments["max_lines"], 1, FILE_READ_MAX_LINES),
                 "read_file max_lines is malformed")
    elif tool == "inspect_system":
        _require(keys == {"operation"}
                 and arguments.get("operation") in ("status", "processes", "context"),
                 "inspect_system arguments are malformed")
    else:
        _fail(f"unadvertised Nexus tool: {tool}")
    return dict(arguments)


def _validate_fixture(
    records: Sequence[dict[str, object]],
    *,
    require_acceptance_scenarios: bool = True,
) -> tuple[FixtureRecord, ...]:
    result: list[FixtureRecord] = []
    seen: set[str] = set()
    finals = 0
    for index, record in enumerate(records, 1):
        _require(set(record) == {"request_sha256", "response"},
                 f"fixture record {index} fields are malformed")
        digest = _digest(record["request_sha256"], f"fixture request {index}")
        _require(digest not in seen, "fixture repeats a provider request digest")
        seen.add(digest)
        response = record["response"]
        _require(isinstance(response, dict), f"fixture response {index} is not an object")
        kind = response.get("type")
        if kind == "final":
            _require(set(response) == {"type", "content"},
                     f"fixture final {index} fields are malformed")
            _bounded_final_text(
                response.get("content"), f"fixture final {index}", MAX_FINAL_BYTES
            )
            finals += 1
        elif kind == "tool_use":
            _require(set(response) == {"type", "tool", "arguments"},
                     f"fixture tool response {index} fields are malformed")
            tool = response.get("tool")
            _require(isinstance(tool, str) and tool in TOOL_SET,
                     f"fixture tool response {index} is unadvertised")
            _validate_tool_arguments(str(tool), response.get("arguments"))
        elif kind == "error":
            _require(set(response) == {"type", "code", "message", "retryable"},
                     f"fixture error response {index} fields are malformed")
            code = response.get("code")
            _require(isinstance(code, str)
                     and MODEL_ERROR_CODE_RE.fullmatch(code) is not None,
                     f"fixture error response {index} code is malformed")
            _bounded_text(
                response.get("message"),
                f"fixture error response {index} message",
                MAX_MODEL_ERROR_MESSAGE_BYTES,
            )
            _require(isinstance(response.get("retryable"), bool),
                      f"fixture error response {index} retryability is malformed")
            _require(
                code not in NEXUS_RETRYABLE_RESPONSE_ERROR_CODES
                or response.get("retryable") is True,
                f"fixture error response {index} contradicts Nexus retry normalization",
            )
        else:
            _fail(f"fixture response {index} has an unsupported type")
        result.append(FixtureRecord(digest, dict(response)))
    if require_acceptance_scenarios:
        _require(finals >= 1, "fixture must contain at least one final response")
    return tuple(result)


def _same_envelope(record: Mapping[str, object], turn: int, request: int) -> None:
    _require(
        record.get("turn_id") == turn and record.get("request_id") == request,
        f"event escaped turn {turn}/request {request}",
    )


def _history_bindings(
    value: object,
    *,
    current_corr: int,
    settled: Sequence[tuple[int, str, str] | None],
) -> tuple[dict[str, object], ...]:
    _require(isinstance(value, list) and len(value) <= MAX_HISTORY_BINDINGS,
             "model history bindings are malformed")
    normalized: list[dict[str, object]] = []
    previous = 0
    available = {entry for entry in settled if entry is not None}
    for item in value:
        _require(
            isinstance(item, dict)
            and set(item)
            == {
                "tool_corr_id",
                "tool",
                "projection_sha256",
                "projection_field",
                "data_trust",
            },
            "model history binding fields are malformed",
        )
        corr = item.get("tool_corr_id")
        tool = item.get("tool")
        digest = item.get("projection_sha256")
        _require(_is_int(corr, 1) and previous < int(corr) < current_corr,
                 "model history correlations are not unique and increasing")
        _require(isinstance(tool, str) and tool in TOOL_SET,
                  "model history names an unadvertised tool")
        expected_projection_field = (
            "model_projection"
            if tool in task_ledger.WORKSPACE_TOOLS
            else "runtime_observation"
        )
        expected_data_trust = (
            "" if tool in task_ledger.WORKSPACE_TOOLS else "guest_runtime_untrusted"
        )
        _require(
            item.get("projection_field") == expected_projection_field
            and item.get("data_trust") == expected_data_trust,
            "model history projection type does not match its tool",
        )
        _digest(digest, "model history projection")
        _require((int(corr), str(tool), str(digest)) in available,
                 "model history is not bound to a settled tool projection")
        normalized.append(dict(item))
        previous = int(corr)
    tail = list(settled[-MAX_HISTORY_BINDINGS:])
    suffixes = (tail[-length:] for length in range(1, len(tail) + 1))
    candidates = [
        [
            {
                "tool_corr_id": corr,
                "tool": tool,
                "projection_sha256": digest,
                "projection_field": (
                    "model_projection"
                    if tool in task_ledger.WORKSPACE_TOOLS
                    else "runtime_observation"
                ),
                "data_trust": (
                    ""
                    if tool in task_ledger.WORKSPACE_TOOLS
                    else "guest_runtime_untrusted"
                ),
            }
            for entry in suffix
            if entry is not None
            for corr, tool, digest in (entry,)
        ]
        for suffix in suffixes
    ]
    if not tail:
        candidates.append([])
    _require(
        normalized in candidates,
        "model history is not a failure-aware contiguous retained suffix",
    )
    return tuple(normalized)


def _wire_response(record: Mapping[str, object]) -> dict[str, object]:
    envelope = {
        "turn_id": record["turn_id"],
        "request_id": record["request_id"],
        "corr_id": record["corr_id"],
    }
    if record.get("response_type") == "tool_use":
        return {
            **envelope,
            "type": "tool_use",
            "tool": record["tool"],
            "arguments": record["arguments"],
        }
    return {**envelope, "type": "final", "content": record["content"]}


def _validate_provider_proof(
    response: Mapping[str, object],
    request: Mapping[str, object],
    fixture: FixtureRecord,
    *,
    provider: str,
    model: str,
) -> None:
    base_required = {
        "type",
        "turn_id",
        "request_id",
        "corr_id",
        *MODEL_PROOF_FIELDS,
        "response_type",
    }
    kind = response.get("response_type")
    if kind == "tool_use":
        variant = {"tool", "arguments"}
    elif kind == "final":
        variant = {
            "content",
            "final_request_sha256",
            "final_response_sha256",
            "provider_proof_sha256",
        }
    else:
        _fail("model_response has an unsupported response_type")
    optional = {"endpoint_origin", "provider_response_id", *RECEIPT_FIELDS}
    _require(base_required | variant <= set(response)
             and not set(response).difference(base_required | variant | optional),
             "model_response proof fields are malformed")
    _require(
        _is_int(
            response.get("user_message_index"),
            0,
            2 * nexus_contract.CONTEXT_PATH_MAX_TURNS,
        ),
        "model_response user_message_index is malformed",
    )
    for key in (
        "turn_id", "request_id", "corr_id", "generation", "request_sha256",
        "raw_guest_request_sha256", "history_bindings", "request_contains_user",
        "user_message_index", "user_content_sha256", "user_bytes",
    ):
        _require(response.get(key) == request.get(key),
                 f"model_response changed request binding {key}")
    _require(response.get("provider") == provider and response.get("model") == model,
             "model_response changed the configured provider identity")
    _require(response.get("adapter_success") is True,
             "model_response is not an adapter success")
    _require(response.get("request_sha256") == fixture.request_sha256,
             "model_response is not bound to the replay request digest")
    _require(response.get("raw_guest_request_sha256") != response.get("request_sha256"),
             "Host-only request contract fields were not stripped before hashing")
    response_sha = _sha(_wire_response(response))
    _require(hmac.compare_digest(
        _digest(response.get("response_sha256"), "model response digest"), response_sha
    ), "model_response digest does not bind the delivered wire bytes")

    expected = fixture.response
    _require(expected.get("type") in ("tool_use", "final"),
             "model_response consumed a non-response fixture record")
    if expected.get("type") == "tool_use":
        _require(kind == "tool_use"
                 and response.get("tool") == expected.get("tool")
                 and response.get("arguments") == expected.get("arguments"),
                 "controller model tool response differs from the fixture")
    else:
        _require(kind == "final" and response.get("content") == expected.get("content"),
                 "controller model final differs from the fixture")

    proof_order = list(MODEL_PROOF_FIELDS)
    receipt_present = any(field in response for field in RECEIPT_FIELDS)
    if provider == "replay":
        _require(response.get("transport") == "replay" and not receipt_present
                 and "endpoint_origin" not in response,
                 "replay response unexpectedly claims an HTTPS receipt")
    else:
        _require(provider == "deepseek" and response.get("transport") == "https",
                 "acceptance capture must use DeepSeek HTTPS or strict replay")
        _require(receipt_present and all(field in response for field in RECEIPT_FIELDS),
                 "live DeepSeek response lacks its complete provider receipt")
        _require(response.get("endpoint_origin") == "https://api.deepseek.com",
                 "DeepSeek endpoint origin changed")
        proof_order.append("endpoint_origin")
        proof_order.extend(RECEIPT_FIELDS)
        if "provider_response_id" in response:
            proof_order.append("provider_response_id")
        _require(response.get("provider_endpoint")
                 == "https://api.deepseek.com/chat/completions",
                 "DeepSeek provider endpoint changed")
        _require(_is_int(response.get("http_status"), 200, 299),
                 "DeepSeek provider receipt has a non-success HTTP status")
        for field in (
            "provider_request_sha256", "provider_response_sha256",
            "selected_reply_sha256",
        ):
            _digest(response.get(field), field)
        _require(response.get("tool_choice_mode") == "auto"
                 and response.get("forced_tool") is None,
                 "DeepSeek was forced to select a tool")
        # A success receipt binds the final HTTPS exchange and reports only a
        # bounded adapter-local attempt count.  It cannot reconstruct the
        # rejected provider response, so do not treat attempt 2 as proof of a
        # particular repair cause or as another Guest-visible model outcome.
        _require(
            _is_int(
                response.get("attempt_count"),
                1,
                MAX_DEEPSEEK_ADAPTER_ATTEMPTS,
            ),
            "DeepSeek success exceeded its bounded provider-local repair",
        )
        raw_count = response.get("raw_tool_call_count")
        selected_index = response.get("selected_index")
        adaptation = response.get("adaptation")
        if kind == "tool_use":
            single_call = (
                _is_int(raw_count, 1, 1)
                and _is_int(selected_index, 0, 0)
                and adaptation == "none"
            )
            serialized_calls = (
                _is_int(raw_count, 2, MAX_RAW_TOOL_CALLS)
                and _is_int(selected_index, 0, int(raw_count) - 1)
                and adaptation == "selected_first_valid_tool_call"
            )
            _require(single_call or serialized_calls,
                     "DeepSeek tool receipt has an invalid auto-selection claim")
            selected = _sha(
                {"tool": response["tool"], "arguments": response["arguments"]}
            )
            _require(hmac.compare_digest(
                _digest(response.get("selected_tool_sha256"), "selected tool digest"),
                selected,
            ), "provider receipt does not bind the selected tool")
        else:
            _require(_is_int(raw_count, 0, 0)
                     and isinstance(selected_index, int)
                     and not isinstance(selected_index, bool)
                     and selected_index == -1
                     and adaptation == "none"
                     and response.get("selected_tool_sha256") == "",
                     "DeepSeek final receipt claims a tool selection")
            selected = _sha({"type": "final", "content": response["content"]})
            _require(hmac.compare_digest(
                _digest(response.get("selected_reply_sha256"), "selected reply digest"),
                selected,
            ), "provider receipt does not bind the selected final")

    if kind == "final":
        _require(response.get("final_request_sha256") == request.get("request_sha256")
                 and response.get("final_response_sha256") == response_sha,
                 "final model response proof is not bound to its request")
        proof_order.append("final_request_sha256")
        proof = {field: response[field] for field in proof_order}
        _require(hmac.compare_digest(
            _digest(response.get("provider_proof_sha256"), "provider proof digest"),
            _sha(proof),
        ), "final provider proof root is malformed")


def _validate_provider_error_proof(
    proof: Mapping[str, object],
    request: Mapping[str, object],
    model_error: Mapping[str, object],
    *,
    provider: str,
    model: str,
) -> None:
    required = {
        "type",
        "turn_id",
        "request_id",
        "corr_id",
        "status",
        "code",
        "generation",
        "provider",
        "model",
        "request_sha256",
        "raw_guest_request_sha256",
        "history_bindings",
        "request_contains_user",
        "user_message_index",
        "user_content_sha256",
        "user_bytes",
        "adapter_success",
        "transport",
        *RECEIPT_FIELDS,
    }
    _require(
        required <= set(proof)
        and not set(proof).difference(required | {"provider_response_id"}),
        "provider error proof fields are malformed",
    )
    _require(provider == "deepseek" and proof.get("provider") == provider
             and proof.get("model") == model,
             "provider error proof changed the configured provider identity")
    _require(
        _is_int(
            proof.get("user_message_index"),
            0,
            2 * nexus_contract.CONTEXT_PATH_MAX_TURNS,
        ),
        "provider error proof user_message_index is malformed",
    )
    for key in (
        "turn_id", "request_id", "corr_id", "generation", "request_sha256",
        "raw_guest_request_sha256", "history_bindings", "request_contains_user",
        "user_message_index", "user_content_sha256", "user_bytes",
    ):
        _require(proof.get(key) == request.get(key),
                 f"provider error proof changed request binding {key}")
    code = model_error.get("code")
    _require(proof.get("type") == "provider_result"
             and proof.get("status") == "error"
             and proof.get("code") == code,
             "provider error proof changed its delivered MODEL_ERROR")
    _require(proof.get("adapter_success") is False
             and proof.get("transport") == "https",
             "provider error proof is not a rejected HTTPS adaptation")
    _require(proof.get("provider_endpoint")
             == "https://api.deepseek.com/chat/completions"
             and _is_int(proof.get("http_status"), 200, 299),
             "DeepSeek error proof has an invalid provider receipt")
    for field in ("provider_request_sha256", "provider_response_sha256"):
        _digest(proof.get(field), field)
    _require(proof.get("selected_reply_sha256") == ""
             and proof.get("selected_tool_sha256") == ""
             and proof.get("selected_index") == -1,
             "rejected provider response claims a selected reply")
    _require(
        _is_int(proof.get("attempt_count"), 1, MAX_DEEPSEEK_ADAPTER_ATTEMPTS)
        and proof.get("tool_choice_mode") == "auto"
        and proof.get("forced_tool") is None
        and _is_int(proof.get("raw_tool_call_count"), 0, MAX_RAW_TOOL_CALLS)
        and proof.get("adaptation") == f"rejected_{str(code).lower()}",
        "DeepSeek error proof changed the autonomous adapter contract",
    )
    if "provider_response_id" in proof:
        _bounded_text(
            proof.get("provider_response_id"),
            "provider response id",
            256,
        )


def _validate_tool_event_shape(record: Mapping[str, object]) -> None:
    tool = record.get("tool")
    status_value = record.get("status")
    _require(set(record) == TOOL_EVENT_FIELDS, "tool_event fields are malformed")
    _require(record.get("tool") in TOOL_SET, "tool_event names an unadvertised tool")
    _require(_is_int(record.get("corr_id"), 1)
             and _is_int(record.get("sequence"), 0)
             and _is_int(record.get("context_seq"), 0),
             "tool_event correlation or sequence is malformed")
    _require(_is_int(record.get("status"), -(1 << 31), (1 << 31) - 1),
             "tool_event status is malformed")
    for field in ("value0", "value1", "value2"):
        _require(_is_int(record.get(field)), f"tool_event {field} is malformed")
    _bounded_text(record.get("result"), "tool_event result", 95, empty=True)
    _digest(record.get("result_sha256"), "tool result digest")
    expected_trust = (
        "untrusted"
        if tool in task_ledger.WORKSPACE_TOOLS
        else ("kernel_fact" if status_value == 0 else "none")
    )
    _require(
        record.get("data_trust") == expected_trust,
        "tool_event trust classification is malformed",
    )
    status = int(record["status"])
    if status == 0:
        _require(record.get("provenance") == SUCCESS_PROVENANCE[record["tool"]],
                 "successful tool_event provenance is malformed")
        _digest(record.get("projection_sha256"), "successful tool projection")
        artifact_digest = _digest(
            record.get("artifact_sha256"), "successful tool artifact"
        )
        _require(
            hmac.compare_digest(
                artifact_digest, str(record.get("projection_sha256"))
            ),
            "tool projection is not bound to its published artifact",
        )
        if tool in ("search_files", "read_file"):
            _bounded_workspace_result(
                record.get("model_projection"), "Guest workspace projection"
            )
            _digest(
                record.get("workspace_source_sha256"),
                "workspace source root",
            )
        else:
            _bounded_workspace_result(
                record.get("model_projection"), "Guest system projection"
            )
            _require(
                record.get("workspace_source_sha256") == "",
                "non-workspace tool exposed a workspace source root",
            )
    else:
        _require(record.get("provenance") == 0
                 and record.get("projection_sha256") == ""
                 and record.get("artifact_sha256") == ""
                 and record.get("workspace_source_sha256") == ""
                 and record.get("model_projection") == ""
                 and all(record.get(field) == 0 for field in ("value0", "value1", "value2")),
                 "failed tool_event exposed successful result bindings")


def _kernel_identities(
    records: Sequence[dict[str, object]],
) -> dict[int, task_ledger.KernelIdentity]:
    identities: dict[int, task_ledger.KernelIdentity] = {}
    roles: dict[str, task_ledger.KernelIdentity] = {}
    audit_sequence = 0
    for record in records:
        if record.get("type") != "telemetry" or record.get("source") not in (
            "kernel_audit", "kernel_snapshot"
        ):
            continue
        role = record.get("role")
        if role not in BUSINESS_ROLES:
            continue
        pid = record.get("pid")
        agent_id = record.get("agent_id")
        control_id = record.get("actor_control_id")
        _require(_is_int(pid, 1, (1 << 31) - 1)
                 and _is_int(agent_id, 1, (1 << 31) - 1)
                 and _is_int(control_id, 1, U64_MAX),
                 "kernel identity telemetry is malformed")
        _require(_is_int(record.get("workflow_lifecycle_id"), 1)
                 and _is_int(record.get("workflow_lifecycle_generation"), 1),
                 "kernel identity lacks a workflow lifecycle")
        if record.get("source") == "kernel_audit":
            _require(record.get("event") == "kernel_audit"
                     and record.get("fresh") is True,
                     "kernel audit telemetry envelope is malformed")
            sequence = record.get("record_sequence")
            _require(_is_int(sequence, 1) and int(sequence) > audit_sequence,
                     "kernel audit sequence is not increasing")
            audit_sequence = int(sequence)
            _require(
                _is_int(record.get("event_type"), 0, MAX_U32)
                and _is_int(record.get("audit_kind"), 0, MAX_U32)
                and _is_int(
                    record.get("provenance"),
                    0,
                    task_ledger.NEXUS_PROVENANCE_ALL,
                ),
                "generic kernel audit metadata is malformed",
            )
        else:
            _require(record.get("event") == "kernel_snapshot"
                     and record.get("fresh") is False,
                     "kernel snapshot telemetry envelope is malformed")
            dispatch = record.get("sched_dispatch")
            dispatch_count = record.get("sched_dispatch_count")
            _require(_is_int(dispatch, 1) and _is_int(dispatch_count)
                     and int(dispatch_count) >= int(dispatch),
                     "kernel scheduler snapshot is inconsistent")
            _require(_is_int(record.get("wait_sleep_delta"), 1)
                     and _is_int(record.get("wait_wakeup_delta"), 1)
                     and _is_int(record.get("sched_budget"), 1)
                     and _is_int(record.get("sched_budget_used"), 1),
                     "kernel runtime snapshot is incomplete")
        identity = task_ledger.KernelIdentity(
            str(role), int(pid), int(agent_id), int(control_id)
        )
        previous = identities.setdefault(int(pid), identity)
        _require(previous == identity, "kernel identity changed for one PID")
        previous_role = roles.setdefault(str(role), identity)
        _require(previous_role == identity, "one Nexus role acquired multiple identities")
    _require("coordinator" in roles,
             "observer did not authenticate the Nexus coordinator identity")
    return identities


def _ledger(
    call,
    label: str,
):
    try:
        return call()
    except task_ledger.NexusTaskLedgerError as error:
        raise ValidationError(f"{label}: {error.code}: {error.reason}") from error


def _turn_slices(
    controller: Sequence[dict[str, object]],
) -> tuple[tuple[int, int, tuple[dict[str, object], ...]], ...]:
    result: list[tuple[int, int, tuple[dict[str, object], ...]]] = []
    start: int | None = None
    envelope: tuple[int, int] | None = None
    for index, record in enumerate(controller):
        kind = record.get("type")
        if kind == "turn_started":
            _require(start is None, "controller started a nested turn")
            turn = record.get("turn_id")
            request = record.get("request_id")
            _require(_is_int(turn, 1, MAX_U32 - task_ledger.NEXUS_ROOT_TASK_BASE)
                     and _is_int(request, 1), "turn_started envelope is malformed")
            start = index
            envelope = (int(turn), int(request))
        elif kind == "turn_complete":
            _require(start is not None and envelope is not None,
                     "controller completed an unknown turn")
            _same_envelope(record, *envelope)
            result.append((envelope[0], envelope[1], tuple(controller[start:index + 1])))
            start = None
            envelope = None
        elif start is not None and kind in (
            "control_result", "session_closing", "session_closed", "session_ready"
        ):
            _fail("an idle/session event appeared inside an active turn")
    _require(start is None and bool(result), "controller has an unterminated or empty turn set")
    return tuple(result)


def _task_identity_fields(record: Mapping[str, object]) -> tuple[str, int, int, int]:
    role = record.get("role")
    pid = record.get("agent_pid")
    agent_id = record.get("agent_id")
    control = record.get("control_id")
    _require(role in BUSINESS_ROLES and record.get("agent_role") == role,
             "TASK_EVENT role aliases are malformed")
    _require(record.get("control_id_known") is True
             and _is_int(pid, 1, (1 << 31) - 1)
             and _is_int(agent_id, 1, (1 << 31) - 1)
             and _is_int(control, 1, U64_MAX)
             and record.get("agent_control_id") == control,
             "TASK_EVENT identity aliases are malformed")
    return str(role), int(pid), int(agent_id), int(control)


def _root_binds_derived_cancel(
    record: Mapping[str, object], *, turn_id: int, corr_id: int
) -> bool:
    """Recognize the root event that authorizes a Task-derived settlement.

    A child cancellation alone is ambiguous: a deadline and a Host interrupt
    may cross on opposite transport directions.  Only the same-correlation
    root terminal disambiguates a suppressed TOOL_EVENT from an ordinary
    deadline TOOL_EVENT that is still in flight.
    """

    common = bool(
        record.get("task_id") == task_ledger.NEXUS_ROOT_TASK_BASE + turn_id
        and record.get("parent_task_id") == 0
        and record.get("corr_id") == corr_id
        and record.get("role") == "coordinator"
        and record.get("agent_role") == "coordinator"
        and record.get("source_pid") == record.get("agent_pid")
        and record.get("target_pid") == record.get("agent_pid")
        and record.get("deadline_tick", 0) == 0
        and record.get("artifact_handle", 0) == 0
        and record.get("digest", "") == ""
        and record.get("artifact_sha256", record.get("digest", "")) == ""
        and record.get("context_seq", 0) == 0
        and record.get("resource_used", 0) == 0
        and record.get("provenance", 0) == 0
        and record.get("metric_code", 0) == 0
        and record.get("metric_value", 0) == 0
    )
    if not common:
        return False
    if (
        record.get("event") == "cancelled"
        and record.get("task_state") == "cancelled"
        and record.get("status") == task_ledger.AGENT_STATUS_CANCELLED
    ):
        return record.get("summary") == "turn_cancelled"
    return bool(
        record.get("event") == "failed"
        and record.get("task_state") == "failed"
        and record.get("status") == task_ledger.AGENT_STATUS_IO_ERROR
        and record.get("summary") == ARTIFACT_CLEANUP_SESSION_BLOCK
    )


def _validate_turn(
    records: Sequence[dict[str, object]],
    *,
    turn_id: int,
    request_id: int,
    generation: int,
    goal: str | None,
    ledger: task_ledger.NexusTaskLedger,
    fixture: Sequence[FixtureRecord],
    fixture_cursor: list[int],
    provider: str,
    model: str,
    max_rounds: int,
    global_corr: list[int],
    child_ids: set[int],
    completed_context_turns: list[_CompletedContextTurn],
    observed_context_digests: dict[tuple[int, int], str],
    context_head_sequence: list[int | None],
) -> TurnReport:
    _require(records[0].get("type") == "turn_started"
             and records[-1].get("type") == "turn_complete",
             "turn slice boundaries are malformed")
    started = records[0]
    _require(set(started) == {
        "type", "turn_id", "request_id", "generation", "user_content_sha256", "user_bytes"
    }, "turn_started fields are malformed")
    _same_envelope(started, turn_id, request_id)
    _require(started.get("generation") == generation
             and _is_int(started.get("user_bytes"), 1, MAX_USER_BYTES),
             "turn_started generation or user byte count is malformed")
    user_digest = _digest(started.get("user_content_sha256"), "turn user digest")
    user_bytes = int(started["user_bytes"])
    if goal is not None:
        _require(len(goal.encode("utf-8")) == user_bytes
                 and hmac.compare_digest(_sha_text(goal), user_digest),
                 "turn_started is not bound to the scripted user goal")

    task_records = [record for record in records if record.get("type") == "task_event"]
    _require(task_records, "turn has no root TASK_EVENT proof")
    first_task = task_records[0]
    lifecycle = (
        first_task.get("workflow_lifecycle_id"),
        first_task.get("workflow_lifecycle_generation"),
    )
    _require(_is_int(lifecycle[0], 1) and _is_int(lifecycle[1], 1),
             "turn TASK_EVENT lifecycle is malformed")
    _ledger(
        lambda: ledger.begin_turn(
            turn_id,
            request_id,
            workflow_lifecycle_id=lifecycle[0],
            workflow_lifecycle_generation=lifecycle[1],
        ),
        "cannot begin replay turn ledger",
    )

    requests: dict[int, dict[str, object]] = {}
    responses: dict[int, dict[str, object]] = {}
    provider_error_proofs: set[int] = set()
    tool_events: dict[int, dict[str, object]] = {}
    task_states: dict[int, tuple[int, str, str]] = {}
    task_for_corr: dict[int, int] = {}
    artifact_for_corr: dict[int, dict[str, object]] = {}
    workspace_task_for_corr: dict[int, int] = {}
    workspace_requests: dict[tuple[int, int], dict[str, object]] = {}
    workspace_results: dict[tuple[int, int], dict[str, object]] = {}
    workspace_content: dict[int, dict[str, object]] = {}
    settled_history: list[tuple[int, str, str] | None] = []
    tool_calls: list[tuple[int, str]] = []
    latest_corr = 0
    final_response: dict[str, object] | None = None
    cancel_requested = False
    cancellation_started = False
    cancelled_active_worker = False
    cancelled_child_corrs: set[int] = set()
    derived_cancel_settlements: set[int] = set()
    pending_cleanup_root: dict[str, object] | None = None
    frozen_final_cleanup_root = False
    attempt_number = 0
    decision_count = 0
    retry_count = 0
    round_limit_pending_corr = 0
    active_context_path: _ContextPath | None = None
    turn_start_context_head = context_head_sequence[0]
    terminal = records[-1]
    terminal_status = terminal.get("status")

    for position, record in enumerate(records[1:], 1):
        kind = record.get("type")
        _require(isinstance(kind, str), "controller event has no type")
        _require(
            pending_cleanup_root is None or kind in ("tool_event", "turn_complete"),
            "records intervened between a cleanup root and its tool/turn terminal",
        )
        if kind not in ("turn_complete",):
            _same_envelope(record, turn_id, request_id)

        if kind == "task_event":
            _require(record.get("workflow_lifecycle_id") == lifecycle[0]
                     and record.get("workflow_lifecycle_generation") == lifecycle[1],
                     "TASK_EVENT changed the session lifecycle")
            role, pid, agent_id, control_id = _task_identity_fields(record)
            del pid, agent_id, control_id
            task_id_value = record.get("task_id")
            parent = record.get("parent_task_id")
            corr = record.get("corr_id")
            event = record.get("event")
            state = record.get("task_state")
            _require(_is_int(task_id_value, 1, MAX_U32)
                     and _is_int(parent, 0, MAX_U32)
                     and _is_int(corr, 1)
                     and event in task_ledger.TASK_EVENTS
                     and state in task_ledger.TASK_STATES,
                     "TASK_EVENT state or identity is malformed")
            task_id_int = int(task_id_value)
            corr_int = int(corr)
            if int(parent) == 0:
                _require(task_id_int == task_ledger.NEXUS_ROOT_TASK_BASE + turn_id
                         and role == "coordinator",
                         "turn root TASK_EVENT identity is malformed")
            elif event == "assigned":
                _require(task_id_int not in child_ids,
                         "child task_id was reused across the session")
                child_ids.add(task_id_int)
                task_for_corr[corr_int] = task_id_int
            if event == "artifact_published":
                _digest(record.get("artifact_sha256"), "TASK artifact digest")
                _require(record.get("provenance") == SUCCESS_PROVENANCE[
                    responses[corr_int]["tool"]
                ], "TASK artifact provenance does not match its tool")
                _require(record.get("artifact_handle", 0) == 0,
                         "transient Nexus tool artifact exposed a handle")
                _require(
                    corr_int not in artifact_for_corr
                    and task_for_corr.get(corr_int) == task_id_int,
                    "TASK artifact changed or duplicated its child binding",
                )
                artifact_for_corr[corr_int] = dict(record)
            root_cancel_binding = bool(
                cancellation_started
                and int(parent) == 0
                and corr_int in cancelled_child_corrs
                and corr_int not in tool_events
                and corr_int not in derived_cancel_settlements
                and responses.get(corr_int, {}).get("response_type") == "tool_use"
                and responses[corr_int].get("tool") in TASK_TOOLS
                and _root_binds_derived_cancel(
                    record, turn_id=turn_id, corr_id=corr_int
                )
            )
            if root_cancel_binding and event == "failed":
                _require(pending_cleanup_root is None,
                         "cleanup root terminal was duplicated")
                pending_cleanup_root = dict(record)
            else:
                if root_cancel_binding:
                    _ledger(
                        lambda corr_id=corr_int:
                        ledger.settle_cancelled_tool_from_task(corr_id),
                        "root-bound cancelled child did not settle its tool",
                    )
                    derived_cancel_settlements.add(corr_int)
                _ledger(
                    lambda value=record: ledger.record_event(value),
                    "TASK_EVENT replay failed",
                )
                task_states[task_id_int] = (corr_int, str(event), str(state))
                if (
                    int(parent) == 0
                    and event == "failed"
                    and state == "failed"
                    and record.get("status") == task_ledger.AGENT_STATUS_IO_ERROR
                    and record.get("summary") == ARTIFACT_CLEANUP_SESSION_BLOCK
                    and final_response is not None
                    and corr_int == final_response.get("corr_id")
                ):
                    frozen_final_cleanup_root = True
            if (
                int(parent) != 0
                and event == "cancelled"
                and corr_int in responses
                and responses[corr_int].get("response_type") == "tool_use"
            ):
                cancelled_child_corrs.add(corr_int)

        elif kind == "model_request":
            _require(set(record) == MODEL_REQUEST_FIELDS,
                     "model_request fields are malformed")
            corr = record.get("corr_id")
            _require(_is_int(corr, 1) and int(corr) > global_corr[0],
                     "model request correlations are not session-increasing")
            corr_int = int(corr)
            global_corr[0] = corr_int
            latest_corr = corr_int
            attempt_number += 1
            _require(
                record.get("attempt") == attempt_number
                <= max_rounds + MAX_RETRIES,
                      "model request attempts are not consecutive and bounded")
            _require(record.get("round") == decision_count + 1 <= max_rounds,
                      "model request decision slot is not retry-stable and bounded")
            context_path = _context_path(record.get("context_path"))
            expected_user_message_index = 2 * len(context_path.turns)
            _require(record.get("generation") == generation
                     and record.get("request_contains_user") is True
                     and _is_int(
                         record.get("user_message_index"),
                         0,
                         2 * nexus_contract.CONTEXT_PATH_MAX_TURNS,
                     )
                     and record.get("user_message_index")
                     == expected_user_message_index
                     and record.get("user_content_sha256") == user_digest
                     and record.get("user_bytes") == user_bytes,
                      "model request is not bound to the current user goal")
            if active_context_path is None:
                if turn_start_context_head is None:
                    turn_start_context_head = context_path.current_user_sequence - 1
                _require(
                    context_path.current_user_sequence > turn_start_context_head,
                    "model request USER node does not follow the Relay Context head",
                )
            else:
                _require(
                    context_path.version == active_context_path.version
                    and context_path.branch_generation
                    == active_context_path.branch_generation
                    and context_path.current_user_sequence
                    == active_context_path.current_user_sequence
                    and context_path.visible_head_sequence
                    >= active_context_path.visible_head_sequence,
                    "model request changed branch/user identity or rewound Context",
                )
            active_context_path = context_path

            recent_context = completed_context_turns[
                -nexus_contract.CONTEXT_PATH_MAX_TURNS:
            ]
            recent_completed = {
                (prior.turn_id, prior.request_id): prior
                for prior in recent_context
            }
            expected_suffix = recent_context[-len(context_path.turns):]
            if context_path.turns:
                _require(
                    tuple(
                        (prior.turn_id, prior.request_id)
                        for prior in context_path.turns
                    )
                    == tuple(
                        (prior.turn_id, prior.request_id)
                        for prior in expected_suffix
                    ),
                    "context_path turns are not the ordered completed-turn suffix",
                )
            for prior in context_path.turns:
                binding_key = (prior.turn_id, prior.request_id)
                completed = recent_completed.get(binding_key)
                _require(
                    completed is not None,
                    "context_path references a turn not recently completed in this session",
                )
                assert completed is not None
                _require(
                    prior.user_sequence == completed.user_sequence
                    and prior.final_sequence == completed.final_sequence,
                    "context_path sequences do not match the completed public turn",
                )
                if completed.pair_sha256 is not None:
                    _require(
                        hmac.compare_digest(prior.sha256, completed.pair_sha256),
                        "context_path digest does not match the completed public turn",
                    )
                else:
                    observed_digest = observed_context_digests.setdefault(
                        binding_key, prior.sha256
                    )
                    _require(
                        hmac.compare_digest(prior.sha256, observed_digest),
                        "context_path changed an opaque completed-turn digest",
                    )
            _digest(record.get("request_sha256"), "model request digest")
            _digest(record.get("raw_guest_request_sha256"), "raw Guest request digest")
            _require(record.get("request_sha256") != record.get("raw_guest_request_sha256"),
                     "model request did not strip Host-only contract fields")
            _history_bindings(
                record.get("history_bindings"),
                current_corr=corr_int,
                settled=settled_history,
            )
            _ledger(lambda corr_id=corr_int: ledger.record_model_request(corr_id),
                    "MODEL_REQUEST replay failed")
            requests[corr_int] = dict(record)

        elif kind == "model_response":
            corr = record.get("corr_id")
            _require(_is_int(corr, 1) and int(corr) == latest_corr
                     and int(corr) in requests and int(corr) not in responses,
                     "model_response has no unique latest request")
            corr_int = int(corr)
            _require(fixture_cursor[0] < len(fixture),
                     "controller used more provider responses than the fixture")
            expected = fixture[fixture_cursor[0]]
            fixture_cursor[0] += 1
            _require(requests[corr_int].get("request_sha256") == expected.request_sha256,
                     "fixture request digest order differs from the controller")
            if record.get("response_type") == "tool_use":
                tool = record.get("tool")
                _require(isinstance(tool, str) and tool in TOOL_SET,
                         "model selected an unadvertised tool")
                arguments = _validate_tool_arguments(str(tool), record.get("arguments"))
                _ledger(
                    lambda corr_id=corr_int, name=str(tool), args=arguments:
                    ledger.record_delivered_tool(
                        corr_id,
                        name,
                        arguments_canonical=_canonical_bytes(args).decode("utf-8"),
                    ),
                    "MODEL_RESPONSE tool replay failed",
                )
                tool_calls.append((corr_int, str(tool)))
                _validate_provider_proof(
                    record,
                    requests[corr_int],
                    expected,
                    provider=provider,
                    model=model,
                )
            elif record.get("response_type") == "final":
                _ledger(lambda corr_id=corr_int: ledger.freeze_provider_final(corr_id),
                        "provider final replay failed")
                _validate_provider_proof(
                    record,
                    requests[corr_int],
                    expected,
                    provider=provider,
                    model=model,
                )
                _bounded_final_text(
                    record.get("content"), "provider final", MAX_FINAL_BYTES
                )
                final_response = dict(record)
            else:
                _fail("model_response has an unsupported response type")
            responses[corr_int] = dict(record)
            decision_count += 1
            if decision_count == max_rounds and record.get("response_type") == "tool_use":
                round_limit_pending_corr = corr_int

        elif kind == "workspace_request":
            _require(
                set(record) == WORKSPACE_REQUEST_FIELDS,
                "workspace_request fields are malformed",
            )
            corr = record.get("corr_id")
            task_id_value = record.get("task_id")
            attempt = record.get("attempt")
            tool = record.get("tool")
            operation = record.get("operation")
            manifest_cursor = record.get("manifest_cursor")
            _require(
                _is_int(corr, 1)
                and int(corr) == latest_corr
                and _is_int(task_id_value, 1, MAX_U32)
                and _is_int(
                    attempt, 1, task_ledger.MAX_WORKSPACE_ATTEMPTS
                )
                and tool in task_ledger.WORKSPACE_TOOLS
                and operation in task_ledger.WORKSPACE_OPERATIONS[str(tool)]
                and _is_int(manifest_cursor, 0, MAX_U32)
                and (
                    operation == "manifest" or int(manifest_cursor) == 0
                )
                and int(corr) in responses
                and responses[int(corr)].get("response_type") == "tool_use"
                and responses[int(corr)].get("tool") == tool,
                "workspace_request has no active delivered workspace tool",
            )
            corr_int = int(corr)
            task_id_int = int(task_id_value)
            attempt_int = int(attempt)
            key = (corr_int, attempt_int)
            _require(
                key not in workspace_requests
                and key not in workspace_results
                and task_for_corr.get(corr_int) is None,
                "workspace_request was duplicated or followed child execution",
            )
            reserved = workspace_task_for_corr.get(corr_int)
            _require(
                (reserved is None or reserved == task_id_int)
                and task_id_int not in child_ids
                and all(
                    other_corr == corr_int or other_task != task_id_int
                    for other_corr, other_task in workspace_task_for_corr.items()
                ),
                "workspace_request changed or reused its reserved child Task",
            )
            workspace_generation_value = _digest(
                record.get("workspace_generation"),
                "workspace request generation",
                empty=True,
            )
            arguments_digest = _digest(
                record.get("arguments_sha256"),
                "workspace request arguments digest",
            )
            objects_digest = _digest(
                record.get("objects_sha256"),
                "workspace request object revisions digest",
            )
            _ledger(
                lambda: ledger.record_workspace_request(
                    corr_int,
                    task_id=task_id_int,
                    tool=str(tool),
                    operation=str(operation),
                    attempt=attempt_int,
                    workspace_generation=workspace_generation_value,
                    arguments_sha256=arguments_digest,
                    objects_sha256=objects_digest,
                    manifest_cursor=manifest_cursor,
                ),
                "WORKSPACE_REQUEST replay failed",
            )
            workspace_task_for_corr[corr_int] = task_id_int
            workspace_requests[key] = dict(record)

        elif kind == "workspace_result":
            _require(
                set(record) == WORKSPACE_RESULT_FIELDS,
                "workspace_result fields are malformed",
            )
            corr = record.get("corr_id")
            attempt = record.get("attempt")
            _require(
                _is_int(corr, 1)
                and _is_int(
                    attempt, 1, task_ledger.MAX_WORKSPACE_ATTEMPTS
                ),
                "workspace_result correlation or attempt is malformed",
            )
            corr_int = int(corr)
            attempt_int = int(attempt)
            key = (corr_int, attempt_int)
            request_record = workspace_requests.get(key)
            _require(
                request_record is not None
                and key not in workspace_results
                and corr_int == latest_corr,
                "workspace_result has no unique current Guest request",
            )
            assert request_record is not None
            for field in (
                "turn_id",
                "request_id",
                "corr_id",
                "task_id",
                "tool",
                "operation",
                "attempt",
                "arguments_sha256",
                "manifest_cursor",
            ):
                _require(
                    record.get(field) == request_record.get(field),
                    f"workspace_result changed request binding {field}",
                )
            workspace_generation_value = _digest(
                record.get("workspace_generation"),
                "workspace result generation",
            )
            content_digest = _digest(
                record.get("content_sha256"),
                "workspace result content digest",
            )
            content_bytes = record.get("content_bytes")
            status = record.get("status")
            manifest_cursor = record.get("manifest_cursor")
            manifest_next_cursor = record.get("manifest_next_cursor")
            manifest_eof = record.get("manifest_eof")
            content_limit = (
                task_ledger.MAX_WORKSPACE_MANIFEST_BYTES
                if record.get("operation") == "manifest"
                else task_ledger.MAX_WORKSPACE_CONTENT_BYTES
            )
            _require(
                status in task_ledger.WORKSPACE_RESULT_STATUSES
                and _is_int(
                    content_bytes, 0, content_limit
                ),
                "workspace_result status or byte count is malformed",
            )
            _require(
                _is_int(manifest_cursor, 0, MAX_U32)
                and _is_int(manifest_next_cursor, 0, MAX_U32)
                and isinstance(manifest_eof, bool),
                "workspace_result manifest paging metadata is malformed",
            )
            _ledger(
                lambda: ledger.record_workspace_result(
                    corr_int,
                    task_id=record["task_id"],
                    tool=record["tool"],
                    operation=record["operation"],
                    attempt=attempt_int,
                    workspace_generation=workspace_generation_value,
                    arguments_sha256=record["arguments_sha256"],
                    result_objects_sha256=record["objects_sha256"],
                    status=status,
                    content_bytes=content_bytes,
                    content_sha256=content_digest,
                    manifest_cursor=manifest_cursor,
                    manifest_next_cursor=manifest_next_cursor,
                    manifest_eof=manifest_eof,
                ),
                "WORKSPACE_RESULT replay failed",
            )
            workspace_results[key] = dict(record)
            if status == "stale":
                workspace_content.pop(corr_int, None)
            target = (
                "search" if record.get("tool") == "search_files" else "read"
            )
            if status == "ok" and record.get("operation") == target:
                workspace_content[corr_int] = dict(record)

        elif kind == "tool_event":
            if pending_cleanup_root is not None:
                cleanup_corr = int(pending_cleanup_root["corr_id"])
                _require(
                    record.get("corr_id") == cleanup_corr
                    and record.get("status") == task_ledger.AGENT_STATUS_IO_ERROR
                    and record.get("value0") == 0
                    and record.get("value1") == 0
                    and record.get("value2") == 0
                    and record.get("provenance") == 0
                    and record.get("projection_sha256") == ""
                    and record.get("result") == ARTIFACT_CLEANUP_SESSION_BLOCK,
                    "cleanup root was not followed by its exact same-correlation tool",
                )
                _ledger(
                    lambda value=pending_cleanup_root: ledger.record_event(value),
                    "cleanup root TASK_EVENT replay failed",
                )
                task_states[int(pending_cleanup_root["task_id"])] = (
                    cleanup_corr,
                    str(pending_cleanup_root["event"]),
                    str(pending_cleanup_root["task_state"]),
                )
                pending_cleanup_root = None
            _validate_tool_event_shape(record)
            corr_int = int(record["corr_id"])
            _require(corr_int in responses
                     and responses[corr_int].get("response_type") == "tool_use"
                     and responses[corr_int].get("tool") == record.get("tool")
                     and corr_int not in tool_events,
                     "tool_event has no unique delivered model tool")
            tool = str(record["tool"])
            status = int(record["status"])
            projection = str(record["projection_sha256"])
            guest_wrapper: dict[str, object] = {
                "status": status,
                "value0": record.get("value0"),
                "value1": record.get("value1"),
                "value2": record.get("value2"),
                "result": record.get("result"),
            }
            if status == 0:
                if tool in ("search_files", "read_file"):
                    accepted = workspace_content.get(corr_int)
                    successful_workspace = [
                        item
                        for (item_corr, _attempt), item in workspace_results.items()
                        if item_corr == corr_int and item.get("status") == "ok"
                    ]
                    _require(
                        successful_workspace
                        and workspace_task_for_corr.get(corr_int)
                        == record.get("value1")
                        and record.get("result") == WORKSPACE_OBSERVATION_RESULT,
                        "workspace tool is not bound to accepted Host content",
                    )
                    expected_source_root = _ledger(
                        lambda: ledger.workspace_source_sha256(corr_int),
                        "workspace source root replay failed",
                    )
                    _require(
                        hmac.compare_digest(
                            str(record.get("workspace_source_sha256")),
                            expected_source_root,
                        ),
                        "workspace aggregate changed its ordered source root",
                    )
                    workspace_result = _bounded_workspace_result(
                        record.get("model_projection"),
                        "Guest workspace projection",
                    )
                    expected_projection = workspace_result
                    _require(
                        len(workspace_result.encode("utf-8"))
                        == record.get("value0")
                        and (
                            tool != "read_file"
                            or (
                                accepted is not None
                                and accepted.get("content_bytes")
                                == record.get("value0")
                                and hmac.compare_digest(
                                    _sha_text(workspace_result),
                                    str(accepted.get("content_sha256")),
                                )
                            )
                        )
                        and hmac.compare_digest(
                            _sha_text(workspace_result),
                            str(record.get("artifact_sha256")),
                        ),
                        "Guest workspace result changed its accepted artifact",
                    )
                    guest_wrapper["model_projection"] = expected_projection
                else:
                    actual_projection = _bounded_workspace_result(
                        record.get("model_projection"),
                        "Guest system projection",
                    )
                    response_arguments = responses[corr_int].get("arguments")
                    _require(
                        isinstance(response_arguments, Mapping),
                        "system observation lost its delivered arguments",
                    )
                    expected_projection = _system_projection(
                        response_arguments.get("operation"),
                        record.get("value0"),
                        record.get("value1"),
                        record.get("value2"),
                    )
                    artifact = artifact_for_corr.get(corr_int)
                    _require(
                        actual_projection == expected_projection
                        and record.get("result") == SYSTEM_OBSERVATION_RESULT
                        and artifact is not None
                        and artifact.get("task_id") == task_for_corr.get(corr_int)
                        and artifact.get("artifact_sha256")
                        == record.get("artifact_sha256")
                        and artifact.get("resource_used")
                        == len(actual_projection.encode("utf-8")),
                        "system observation is not bound to its SYSTEM_RESULT artifact",
                    )
                    guest_wrapper["model_projection"] = expected_projection
                _require(
                    hmac.compare_digest(projection, _sha_text(expected_projection)),
                    "tool projection does not match its exact Guest data",
                )
            _require(
                hmac.compare_digest(
                    str(record.get("result_sha256")), _sha(guest_wrapper)
                ),
                "tool result digest does not match its exact Guest wrapper",
            )
            _ledger(
                lambda corr_id=corr_int, name=tool, event=record:
                ledger.settle_tool(
                    corr_id,
                    tool=name,
                    status=event["status"],
                    value0=event["value0"],
                    value1=event["value1"],
                    value2=event["value2"],
                    provenance=event["provenance"],
                    projection_sha256=event["projection_sha256"],
                    workspace_source_sha256=event["workspace_source_sha256"],
                    context_seq=event["context_seq"],
                    result_sha256=event["result_sha256"],
                    session_blocked_marker=(
                        event["result"]
                        if event["result"] == ARTIFACT_CLEANUP_SESSION_BLOCK
                        else ""
                    ),
                ),
                "TOOL_EVENT replay failed",
            )
            if (
                corr_int == round_limit_pending_corr
                and not ledger.snapshot().termination_cause
            ):
                _ledger(
                    lambda corr_id=corr_int:
                    ledger.begin_termination(corr_id, "round_limit"),
                    "settled final-round tool could not arm round-limit termination",
                )
            tool_events[corr_int] = dict(record)
            if status == 0:
                settled_history.append((corr_int, tool, projection))
            else:
                settled_history.append(None)

        elif kind == "turn_cancelling":
            _require(set(record) == {"type", "turn_id", "request_id"}
                     and not cancel_requested,
                     "turn_cancelling is malformed or duplicated")
            cancel_snapshot = ledger.snapshot()
            terminal_outcome_owned = bool(
                cancel_snapshot.termination_cause
                or cancel_snapshot.provider_final_frozen
            )
            if not terminal_outcome_owned:
                _require(latest_corr in responses,
                         "cancel has no delivered model outcome")
                active_children = [
                    task_id
                    for task_id, (corr, event, state) in task_states.items()
                    if corr == latest_corr
                    and task_id != task_ledger.NEXUS_ROOT_TASK_BASE + turn_id
                    and event not in task_ledger.TASK_TERMINALS
                    and state not in task_ledger.TASK_TERMINALS
                ]
                _require(bool(active_children) or latest_corr in cancelled_child_corrs,
                         "cancel was not bound to active or just-cancelled worker work")
                _require(responses[latest_corr].get("tool") in TASK_TOOLS,
                         "cancel was not directed at an active worker task")
                _ledger(lambda: ledger.begin_cancel(latest_corr),
                        "active worker cancellation replay failed")
                cancellation_started = True
                cancelled_active_worker = True
            cancel_requested = True

        elif kind == "turn_complete":
            _require(position == len(records) - 1,
                     "controller emitted records after turn_complete")
            if pending_cleanup_root is not None:
                cleanup_corr = int(pending_cleanup_root["corr_id"])
                _require(
                    terminal_status == "error"
                    and cleanup_corr in cancelled_child_corrs
                    and cleanup_corr not in tool_events
                    and cleanup_corr not in derived_cancel_settlements,
                    "tool-less cleanup root lacks one cancel-derived terminal outcome",
                )
                _ledger(
                    lambda corr_id=cleanup_corr:
                    ledger.settle_cancelled_tool_from_task(corr_id),
                    "tool-less cleanup root could not settle its cancelled child",
                )
                derived_cancel_settlements.add(cleanup_corr)
                _ledger(
                    lambda value=pending_cleanup_root: ledger.record_event(value),
                    "tool-less cleanup root TASK_EVENT replay failed",
                )
                task_states[int(pending_cleanup_root["task_id"])] = (
                    cleanup_corr,
                    str(pending_cleanup_root["event"]),
                    str(pending_cleanup_root["task_state"]),
                )
                pending_cleanup_root = None
        elif kind == "model_error":
            _require(set(record) == {
                "type", "turn_id", "request_id", "corr_id", "code"
            }, "model_error fields are malformed")
            corr = record.get("corr_id")
            _require(_is_int(corr, 1) and int(corr) == latest_corr
                     and int(corr) in requests and int(corr) not in responses,
                     "model_error has no unique latest request")
            corr_int = int(corr)
            _require(fixture_cursor[0] < len(fixture),
                     "controller used more provider outcomes than the fixture")
            expected = fixture[fixture_cursor[0]]
            fixture_cursor[0] += 1
            _require(requests[corr_int].get("request_sha256") == expected.request_sha256,
                     "fixture request digest order differs from the controller")
            error = expected.response
            _require(error.get("type") == "error"
                     and record.get("code") == error.get("code"),
                     "controller model_error differs from the fixture")
            retryable = error.get("retryable")
            assert isinstance(retryable, bool)
            _ledger(
                lambda corr_id=corr_int, may_retry=retryable:
                ledger.record_model_error(corr_id, retryable=may_retry),
                "MODEL_ERROR replay failed",
            )
            if retryable:
                retry_count += 1
                _require(retry_count <= MAX_RETRIES,
                         "model retry count exceeds the negotiated budget")
                if retry_count == MAX_RETRIES:
                    _ledger(
                        lambda corr_id=corr_int:
                        ledger.begin_termination(corr_id, "round_limit"),
                        "MODEL_ERROR retry limit replay failed",
                    )
            else:
                _ledger(
                    lambda corr_id=corr_int:
                    ledger.begin_termination(corr_id, "provider_fatal"),
                    "fatal MODEL_ERROR replay failed",
                )
            responses[corr_int] = dict(record)

        elif kind == "provider_result":
            corr = record.get("corr_id")
            _require(
                _is_int(corr, 1)
                and int(corr) == latest_corr
                and int(corr) in requests
                and responses.get(int(corr), {}).get("type") == "model_error"
                and int(corr) not in provider_error_proofs
                and records[position - 1].get("type") == "model_error"
                and records[position - 1].get("corr_id") == corr,
                "provider error proof is not adjacent to a unique MODEL_ERROR",
            )
            corr_int = int(corr)
            _validate_provider_error_proof(
                record,
                requests[corr_int],
                responses[corr_int],
                provider=provider,
                model=model,
            )
            provider_error_proofs.add(corr_int)

        elif kind == "model_request_dropped":
            _fail("deterministic acceptance cannot contain a dropped model request")
        else:
            _fail(f"unsupported controller event inside turn: {kind}")

    _require(pending_cleanup_root is None,
             "cleanup root was not committed before TURN_COMPLETE")
    _require(
        _is_int(terminal.get("rounds"), 0, max_rounds)
        and terminal.get("rounds") == decision_count,
        "TURN_COMPLETE round count is malformed",
    )
    _require(
        _is_int(terminal.get("retries"), 0, MAX_RETRIES)
        and terminal.get("retries") == retry_count,
        "TURN_COMPLETE retry count is malformed",
    )
    _require(
        _is_int(terminal.get("attempts"), 1, max_rounds + MAX_RETRIES)
        and terminal.get("attempts") == attempt_number,
        "TURN_COMPLETE attempt count is malformed",
    )
    _require(active_context_path is not None,
             "turn completed without an authenticated Context path")
    _require(_is_int(terminal.get("context_seq"), 0, U64_MAX),
             "TURN_COMPLETE context sequence is malformed")
    required_terminal = {
        "type", "turn_id", "request_id", "status", "final_proof_root",
        "rounds", "retries", "attempts", "context_seq", *FINAL_PROOF_FIELDS,
    }
    allowed_terminal = set(required_terminal)
    if terminal_status == "completed":
        allowed_terminal.add("answer")
    _require(required_terminal <= set(terminal)
             and not set(terminal).difference(allowed_terminal),
             "TURN_COMPLETE fields are malformed")
    _require(terminal_status in ("completed", "cancelled", "error"),
             "TURN_COMPLETE status is unsupported")
    snapshot = _ledger(
        lambda: ledger.assert_turn_complete(str(terminal_status)),
        "TURN_COMPLETE task proof replay failed",
    )
    _require(terminal.get("version") == 1
             and terminal.get("final_task_root") == snapshot.task_root_sha256
             and terminal.get("final_artifact_root") == snapshot.artifact_root_sha256,
             "TURN_COMPLETE task/artifact roots do not replay")
    preserved_final_proof = bool(
        final_response is not None
        and terminal.get("final_corr_id") == final_response.get("corr_id")
        and terminal.get("final_request_sha256")
        == final_response.get("final_request_sha256")
        and terminal.get("final_response_sha256")
        == final_response.get("final_response_sha256")
        and terminal.get("provider_proof_sha256")
        == final_response.get("provider_proof_sha256")
    )
    if terminal_status == "completed":
        _require(
            preserved_final_proof
            and terminal.get("answer") == final_response.get("content"),
            "TURN_COMPLETE did not preserve the exact provider final proof",
        )
        final_content = str(terminal["answer"])
        assert active_context_path is not None
        final_context_sequence = int(terminal["context_seq"])
        _require(
            final_context_sequence > active_context_path.visible_head_sequence,
            "TURN_COMPLETE final Context node does not follow the visible request path",
        )
    else:
        context_final_failed = snapshot.termination_cause == CONTEXT_FINAL_FAILED
        frozen_final_cleanup_failed = bool(
            frozen_final_cleanup_root
            and snapshot.provider_final_frozen
            and snapshot.termination_cause == "session_error"
            and snapshot.session_blocked
        )
        if context_final_failed:
            _require(
                terminal_status == "error"
                and preserved_final_proof
                and snapshot.provider_final_frozen
                and not snapshot.session_blocked,
                "Context FINAL failure did not preserve its frozen provider proof",
            )
        elif frozen_final_cleanup_failed:
            _require(
                terminal_status == "error" and preserved_final_proof,
                "frozen-final cleanup failure did not preserve its provider proof",
            )
        else:
            _require(
                final_response is None,
                "non-completed turn retained an unauthorized provider final",
            )
        _require("answer" not in terminal,
                 "non-completed turn exposed a final answer")
        assert active_context_path is not None
        assert turn_start_context_head is not None
        _require(
            int(terminal["context_seq"]) == turn_start_context_head,
            "non-completed turn did not restore its exact starting Context head",
        )
        final_content = ""
    context_head_sequence[0] = int(terminal["context_seq"])
    final_values = {field: terminal[field] for field in FINAL_PROOF_FIELDS}
    _require(hmac.compare_digest(
        _digest(terminal.get("final_proof_root"), "final proof root"),
        _sha(final_values),
    ), "TURN_COMPLETE final proof root is malformed")

    if terminal_status == "completed":
        assert active_context_path is not None
        # Retain sequence metadata and, when a scripted goal is available, only
        # the one-way pair binding.  Prior message bodies never enter Host proof state.
        completed_context_turns.append(
            _CompletedContextTurn(
                turn_id=turn_id,
                request_id=request_id,
                user_sequence=active_context_path.current_user_sequence,
                final_sequence=int(terminal["context_seq"]),
                pair_sha256=(
                    _pair_sha256(goal, final_content) if goal is not None else None
                ),
            )
        )

    direct_final = bool(
        terminal_status == "completed"
        and decision_count == 1
        and not tool_calls
        and snapshot.task_count == 1
    )
    return TurnReport(
        turn_id=turn_id,
        request_id=request_id,
        status=str(terminal_status),
        request_count=attempt_number,
        tool_calls=tuple(tool_calls),
        direct_final=direct_final,
        cancel_requested=cancel_requested,
        cancelled_active_worker=cancelled_active_worker,
        final_content=final_content,
    )


def _observer_projection(payload: Mapping[str, object], *, source: str) -> dict[str, object]:
    value: dict[str, object] = {}
    for key in OBSERVER_SAFE_FIELDS:
        if key not in payload:
            continue
        item = payload[key]
        if key == "history_bindings":
            if isinstance(item, list) and len(item) <= MAX_HISTORY_BINDINGS:
                value[key] = [dict(binding) for binding in item]
            continue
        if key == "context_path":
            value[key] = _context_path_projection(
                _context_path(item, "observer context_path")
            )
            continue
        if key == "forced_tool" and item is None:
            value[key] = None
        elif isinstance(item, bool):
            value[key] = item
        elif (
            isinstance(item, int)
            and not isinstance(item, bool)
            and (
                (key in CONTROL_ID_FIELDS and 0 <= item <= U64_MAX)
                or (key not in CONTROL_ID_FIELDS and -(1 << 63) <= item <= MAX_U64)
            )
        ):
            value[key] = item
        elif isinstance(item, str):
            try:
                encoded = item.encode("utf-8")
            except UnicodeEncodeError:
                continue
            if len(encoded) <= 256 and not any(
                ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in item
            ):
                value[key] = item
    value["source"] = source
    return {"type": "telemetry", **value}


def _controller_observer_projection(
    record: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    kind = record.get("type")
    if kind == "turn_started":
        return (_observer_projection({"event": "turn_started", **record}, source="host"),)
    if kind == "model_request":
        payload = {
            key: value
            for key, value in record.items()
            if key not in ("type", "round", "attempt")
        }
        return (_observer_projection(
            {"event": "llm_request", "state": "WAITING_LLM", **payload},
            source="host",
        ),)
    if kind == "model_response":
        payload = {
            key: value for key, value in record.items()
            if key not in ("type", "response_type", "arguments", "content")
        }
        provider_payload = {key: value for key, value in payload.items() if key != "tool"}
        provider_result = _observer_projection(
            {"event": "provider_result", **provider_payload, "status": record.get("response_type")},
            source="host",
        )
        model_response = _observer_projection(
            {"event": "model_response", **payload, "status": record.get("response_type")},
            source="host",
        )
        return (provider_result, model_response)
    if kind == "model_error":
        return (_observer_projection(
            {
                "event": "model_error",
                "turn_id": record.get("turn_id"),
                "request_id": record.get("request_id"),
                "corr_id": record.get("corr_id"),
                "status": record.get("code"),
            },
            source="host",
        ),)
    if kind == "provider_result" and record.get("status") == "error":
        return (_observer_projection(
            {"event": "provider_result", **record}, source="host"
        ),)
    if kind == "turn_cancelling":
        return (_observer_projection(
            {"event": "turn_cancel", **record}, source="host"
        ),)
    if kind == "workspace_request":
        return (_observer_projection(
            {"event": "workspace_request", **record}, source="guest"
        ),)
    if kind == "workspace_result":
        return (_observer_projection(
            {"event": "workspace_result", **record}, source="host"
        ),)
    if kind == "tool_event":
        payload = {key: value for key, value in record.items() if key != "result"}
        payload["event"] = "tool_event"
        return (_observer_projection(payload, source="guest"),)
    if kind == "task_event":
        return (_observer_projection(record, source="guest"),)
    if kind == "turn_complete":
        return (_observer_projection({"event": "turn_complete", **record}, source="host"),)
    if kind == "session_closing":
        return (_observer_projection({"event": "session_closing", **record}, source="host"),)
    if kind == "session_closed":
        return (_observer_projection({"event": "session_closed", **record}, source="host"),)
    return ()


def _validate_observer(
    observer: Sequence[dict[str, object]],
    controller: Sequence[dict[str, object]],
    session_id: str,
) -> None:
    _require(observer and observer[0].get("type") == "telemetry"
             and observer[0].get("event") == "observer_attached"
             and observer[0].get("source") == "host"
             and observer[0].get("state") == "IDLE"
             and observer[0].get("turn_id") == 0
             and observer[0].get("request_id") == 0
             and observer[0].get("session_id") == session_id
             and observer[0].get("guest_profile") == "nexus",
             "observer did not attach to the idle Nexus session")
    for record in observer:
        _require(record.get("type") == "telemetry"
                 and set(record).issubset(OBSERVER_SAFE_FIELDS | {"type"}),
                 "observer record escaped the metadata allowlist")
        _require(not set(record).intersection(OBSERVER_FORBIDDEN_KEYS),
                 "observer leaked controller-only content")
        if "context_path" in record:
            _context_path(record.get("context_path"), "observer context_path")
    expected = [
        projection
        for record in controller
        for projection in _controller_observer_projection(record)
    ]
    cursor = 0
    for record in observer[1:]:
        if cursor < len(expected) and record == expected[cursor]:
            cursor += 1
            continue
        _require(record.get("source") in (
            "kernel_audit", "kernel_snapshot", "guest_policy",
            "context_timeline", "context_snapshot",
        ), "observer metadata differs from the immutable controller projection")
    _require(cursor == len(expected),
             "observer missed or reordered controller-visible proof events")
    _require(observer[-1].get("event") == "session_closed",
             "observer emitted telemetry after session_closed")


def _validate_controls(records: Sequence[dict[str, object]]) -> None:
    controls = [record for record in records if record.get("type") == "control_result"]
    commands = [record.get("command") for record in controls]
    _require(commands.count("tools") == 1,
             "controller lacks its unique successful /tools result")
    previous_request = 0
    for record in controls:
        _require(set(record).issubset(
            {"type", "request_id", "command", "status", "result", "code", "message"}
        ) and record.get("status") == "ok"
        and _is_int(record.get("request_id"), 1)
        and int(record["request_id"]) > previous_request,
        "control_result envelope is malformed")
        previous_request = int(record["request_id"])
        result = record.get("result")
        _require(isinstance(result, dict), "control_result lacks a structured result")
        if record.get("command") == "tools":
            catalog = result.get("tools")
            _require(isinstance(catalog, list)
                     and _canonical_bytes(catalog) == _canonical_bytes(list(nexus_contract.TOOLS)),
                     "/tools does not expose the exact v4 Host contract")
        elif record.get("command") == "status":
            _require(set(result) == {
                "tick", "loop_state", "call_count", "wait_sleep", "wait_wakeup",
                "capability_mask",
            } and _is_int(result.get("tick"))
            and _is_int(result.get("call_count"))
            and _is_int(result.get("wait_sleep"))
            and _is_int(result.get("wait_wakeup"))
            and _is_int(result.get("capability_mask"), 1),
            "/status result is malformed")
        else:
            _require(set(result) == {
                "count", "oldest_sequence", "latest_sequence", "dropped",
                "provenance", "detail",
            }, f"/{record.get('command')} result is malformed")
            for key in ("count", "oldest_sequence", "latest_sequence", "dropped", "provenance"):
                _require(_is_int(result.get(key)), f"/{record.get('command')} {key} is malformed")
            _bounded_text(result.get("detail"), f"/{record.get('command')} detail", 256, empty=True)


def validate_records(
    controller: Sequence[dict[str, object]],
    observer: Sequence[dict[str, object]],
    fixture_records: Sequence[dict[str, object]],
    *,
    goals: Sequence[str] | None = None,
    require_acceptance_scenarios: bool = True,
) -> ValidationSummary:
    """Validate already-decoded records.  Kept public for mutation tests."""

    fixture = _validate_fixture(
        fixture_records,
        require_acceptance_scenarios=require_acceptance_scenarios,
    )
    _require(
        len(controller) >= 2
        and controller[0] == {"type": "welcome", "role": "controller"}
        and controller[1].get("type") == "session_ready"
        and len([record for record in controller if record.get("type") == "welcome"]) == 1,
        "controller lacks its exact initial welcome/session_ready handshake",
    )
    ready = controller[1]
    _require(set(ready) == {
        "type", "session_id", "max_rounds", "max_retries", "provider", "model",
        "guest_profile"
    } and isinstance(ready.get("session_id"), str)
    and SESSION_RE.fullmatch(str(ready["session_id"])) is not None
    and _is_int(ready.get("max_rounds"), 1, MAX_ROUNDS)
    and ready.get("max_retries") == MAX_RETRIES
    and ready.get("guest_profile") == "nexus",
    "session_ready is malformed")
    provider = ready.get("provider")
    model = ready.get("model")
    max_rounds = int(ready["max_rounds"])
    _require(provider in ("deepseek", "replay") and isinstance(model, str),
             "session_ready provider/model is unsupported")
    if provider == "deepseek":
        _require(bool(model), "live DeepSeek capture lacks a configured model")
    else:
        _require(model == "", "strict replay must not invent a model identity")

    _validate_controls(controller)
    slices = _turn_slices(controller)
    reset_epoch = 0
    reset_epoch_by_turn: dict[tuple[int, int], int] = {}
    for record in controller:
        if (
            record.get("type") == "control_result"
            and record.get("command") == "reset"
            and record.get("status") == "ok"
        ):
            reset_epoch += 1
        elif record.get("type") == "turn_started":
            reset_epoch_by_turn[
                (int(record["turn_id"]), int(record["request_id"]))
            ] = reset_epoch
    if goals is not None:
        _require(len(goals) == len(slices),
                 "scripted goals and controller turns differ")
    identities = _kernel_identities(observer)
    ledger = task_ledger.NexusTaskLedger(require_kernel_identity=True)
    for identity in identities.values():
        _ledger(
            lambda value=identity: ledger.set_kernel_identity(
                role=value.role,
                pid=value.pid,
                agent_id=value.agent_id,
                control_id=value.control_id,
            ),
            "cannot install a kernel identity",
        )
    fixture_cursor = [0]
    global_corr = [0]
    generation = 1
    child_ids: set[int] = set()
    completed_context_turns: list[_CompletedContextTurn] = []
    observed_context_digests: dict[tuple[int, int], str] = {}
    context_head_sequence: list[int | None] = [None]
    session_blocked = False
    reports: list[TurnReport] = []
    previous_turn = 0
    previous_request = 0
    active_reset_epoch = 0
    for index, (turn, request, records) in enumerate(slices):
        _require(turn > previous_turn and request > previous_request,
                 "turn/request ids are not session-increasing")
        turn_reset_epoch = reset_epoch_by_turn[(turn, request)]
        if turn_reset_epoch != active_reset_epoch:
            _require(
                turn_reset_epoch > active_reset_epoch,
                "successful reset epochs moved backwards",
            )
            completed_context_turns.clear()
            observed_context_digests.clear()
            context_head_sequence[0] = 0
            active_reset_epoch = turn_reset_epoch
        report = _validate_turn(
            records,
            turn_id=turn,
            request_id=request,
            generation=generation,
            goal=(goals[index] if goals is not None else None),
            ledger=ledger,
            fixture=fixture,
            fixture_cursor=fixture_cursor,
            provider=str(provider),
            model=str(model),
            max_rounds=max_rounds,
            global_corr=global_corr,
            child_ids=child_ids,
            completed_context_turns=completed_context_turns,
            observed_context_digests=observed_context_digests,
            context_head_sequence=context_head_sequence,
        )
        reports.append(report)
        snapshot = ledger.snapshot()
        if snapshot.session_blocked:
            session_blocked = True
            _require(
                index == len(slices) - 1 and report.status == "error",
                "an indeterminate Nexus session attempted another turn or non-error close",
            )
        ledger.clear()
        # Host generations fence both turn admission and terminal cleanup.
        # Every observed CANCEL installs one additional fence, even when a
        # previously owned terminal outcome remains authoritative.
        generation += 3 if report.cancel_requested else 2
        previous_turn, previous_request = turn, request

    _require(fixture_cursor[0] == len(fixture),
             "Guest completed before the deterministic provider fixture was exhausted")
    closing_records = [
        record for record in controller if record.get("type") == "session_closing"
    ]
    closed_records = [
        record for record in controller if record.get("type") == "session_closed"
    ]
    _require(
        len(closed_records) == 1
        and controller[-1].get("type") == "session_closed",
        "controller session did not close cleanly and exactly once",
    )
    if session_blocked:
        _require(
            controller[-1].get("reason") == "session_error"
            and len(closing_records) <= 1
            and (
                not closing_records
                or (
                    controller[-2].get("type") == "session_closing"
                    and controller[-2].get("reason") == "user_requested"
                )
            ),
            "blocked controller session did not close with its exact error fence",
        )
    else:
        _require(
            len(closing_records) == 1
            and controller[-2].get("type") == "session_closing"
            and controller[-2].get("reason") == "user_requested"
            and controller[-1].get("reason") == "guest_complete",
            "controller session did not close cleanly and exactly once",
        )
    _validate_observer(observer, controller, str(ready["session_id"]))

    if require_acceptance_scenarios:
        _require(bool(reports), "deterministic acceptance has no natural task")
    return ValidationSummary(len(fixture), tuple(reports), str(provider))


def validate_paths(
    *,
    controller_path: Path,
    observer_path: Path,
    fixture_path: Path,
    script_path: Path = DEFAULT_SCRIPT,
    workspace_root: Path = DEFAULT_WORKSPACE,
    require_acceptance_scenarios: bool = True,
) -> ValidationSummary:
    try:
        script = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError("Nexus replay script is unavailable") from error
    goals = _validate_script_text(script)
    try:
        workspace_info = workspace_root.lstat()
    except OSError as error:
        raise ValidationError("Nexus workspace root is unavailable") from error
    _require(
        stat.S_ISDIR(workspace_info.st_mode) and not workspace_root.is_symlink(),
        "Nexus workspace root must be a non-symlink directory",
    )
    return validate_records(
        _load_jsonl(controller_path, "controller transcript"),
        _load_jsonl(observer_path, "observer transcript"),
        _load_jsonl(fixture_path, "replay fixture"),
        goals=goals,
        require_acceptance_scenarios=require_acceptance_scenarios,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate controller and observer NDJSON from autonomous Nexus."
    )
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        summary = validate_paths(
            controller_path=args.controller,
            observer_path=args.observer,
            fixture_path=args.fixture,
            script_path=args.script,
            workspace_root=args.workspace_root,
        )
    except ValidationError as error:
        print(f"agentos-nexus-replay: FAIL: {error}", file=sys.stderr)
        return 1
    tool_counts = Counter(
        tool for report in summary.turns for _corr_id, tool in report.tool_calls
    )
    tool_summary = ",".join(
        f"{tool}:{tool_counts[tool]}" for tool in TOOL_NAMES if tool_counts[tool]
    ) or "direct-final"
    print(
        "agentos-nexus-replay: PASS "
        f"({summary.fixture_records} provider rounds, {len(summary.turns)} tasks, "
        f"{tool_summary})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
