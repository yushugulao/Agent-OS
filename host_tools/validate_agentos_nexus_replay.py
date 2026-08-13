#!/usr/bin/env python3
"""Validate one autonomous AgentOS Nexus controller/observer replay.

The acceptance artifact is intentionally task-agnostic.  It proves that the
model chose among the five public tools, recovered from an ordinary tool
failure, bound source claims to the configured source snapshot, preserved its
own report bytes, and returned the provider's final bytes exactly.  It does
not prescribe a business conclusion or a fixed specialist route.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
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
import agentos_source_attestation as source_attestation  # noqa: E402


ROOT = _HERE.parent
DEFAULT_SCRIPT = ROOT / "ci" / "agentos-nexus-script.txt"
DEFAULT_CORPUS = ROOT / "user" / "build" / "nexus-source-corpus"
DEFAULT_ANCHOR = (
    ROOT / "user" / "build" / "generated" / "agent_nexus_source_anchor.h"
)

MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 65_536
MAX_USER_BYTES = 2048
MAX_FINAL_BYTES = 2048
MAX_ROUNDS = 16
MAX_RETRIES = 32
MAX_PROVIDER_ATTEMPTS = MAX_ROUNDS + MAX_RETRIES
MAX_MODEL_ERROR_MESSAGE_BYTES = 240
MAX_HISTORY_BINDINGS = 4
MAX_U64 = (1 << 63) - 1
U64_MAX = (1 << 64) - 1
MAX_U32 = (1 << 32) - 1
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
CITATION_RE = re.compile(r"\[S[0-9]{4}:L[1-9][0-9]*-L[1-9][0-9]*\]")
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
SOURCE_SEARCH_NOT_FOUND_STATUS = -5
SOURCE_SEARCH_NO_MATCHES_RESULT = "source_search_no_matches;replan_allowed=1"
SOURCE_SEARCH_SUCCESS_RESULT = "source_evidence_ready;transient=1"

# These strings belonged to the former scripted Live Query.  Their presence
# would turn a protocol fixture back into a frozen business answer.
STALE_PATTERNS = (
    "tool_search",
    "delegate_task",
    "publish_report",
    "approval_request",
    "approval_decision",
    "nexus_meas",
    "agentos live query",
    "publication=denied",
    "3.118",
    "16/16",
    "13.452",
    "33.477",
    "outer_path_erases_gain",
)

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
    "final_evidence_root",
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
    )
)
EVIDENCE_FIELDS = frozenset(
    (
        "type",
        "version",
        "turn_id",
        "request_id",
        "corr_id",
        "task_id",
        "provenance",
        "event",
        "tool",
        "scope",
        "corpus_revision",
        "manifest_sha256",
        "source_id",
        "path",
        "start_line",
        "end_line",
        "citation",
        "full_sha256",
        "chunk_sha256",
        "artifact_sha256",
        "projection_sha256",
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
        "retries",
        "max_retries",
        "request_sha256",
        "raw_guest_request_sha256",
        "history_bindings",
        "evidence_event",
        "scope",
        "corpus_revision",
        "manifest_sha256",
        "source_id",
        "path",
        "start_line",
        "end_line",
        "citation",
        "full_sha256",
        "chunk_sha256",
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
        "final_evidence_root",
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
        "nonce",
        "objective",
        "raw",
        "result",
        "summary",
        "system",
        "tools",
        "user_message",
    )
)


class ValidationError(ValueError):
    """A captured Nexus artifact violates the acceptance contract."""


@dataclass(frozen=True)
class FixtureRecord:
    request_sha256: str
    response: dict[str, object]


@dataclass(frozen=True)
class TurnReport:
    turn_id: int
    request_id: int
    status: str
    request_count: int
    tool_calls: tuple[tuple[int, str], ...]
    direct_final: bool
    source_miss: bool
    source_recovered: bool
    source_read: bool
    runtime: bool
    report_roundtrip: bool
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
        len(raw) <= maximum and escaped_bytes <= maximum,
        f"{label} exceeds its raw or escaped Guest budget",
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


def _reject_stale(value: object, label: str) -> None:
    rendered = json.dumps(value, ensure_ascii=False, allow_nan=False).lower()
    for pattern in STALE_PATTERNS:
        _require(pattern not in rendered, f"{label} freezes obsolete Live Query content: {pattern}")


def _validate_script_text(text: str) -> tuple[str, ...]:
    _require(not text.startswith("\ufeff"), "Nexus script has a UTF-8 BOM")
    _reject_stale(text, "Nexus script")
    lines = [line.rstrip("\r") for line in text.split("\n") if line.rstrip("\r")]
    _require(lines and all(not line.startswith("#") for line in lines),
             "Nexus script comments are executable user turns")
    _require(lines[0] == "/tools" and lines[-1] == "/quit",
             "Nexus script must inspect the tool contract first and close explicitly")
    allowed = {"/tools", "/status", "/context", "/tasks", "/artifacts", "/quit"}
    _require(all(not line.startswith("/") or line in allowed for line in lines),
             "Nexus script contains an unsupported or effectful command")
    commands = [line for line in lines if line.startswith("/")]
    for command in ("/tools", "/status", "/context", "/tasks", "/artifacts", "/quit"):
        _require(command in commands, f"Nexus script is missing {command}")
    goals = tuple(line for line in lines if not line.startswith("/"))
    _require(len(goals) == 2, "Nexus replay must submit exactly two natural user tasks")
    for goal in goals:
        _bounded_text(goal, "scripted user goal", MAX_USER_BYTES)
    return goals


def _validate_tool_arguments(tool: str, arguments: object) -> dict[str, object]:
    _require(isinstance(arguments, dict), f"{tool} arguments must be an object")
    keys = set(arguments)
    if tool == "source_search":
        _require({"query"} <= keys <= {"query", "path_prefix"},
                 "source_search arguments are malformed")
        _bounded_tool_text(arguments["query"], "source_search.query", 95)
        if "path_prefix" in arguments:
            _bounded_tool_text(
                arguments["path_prefix"],
                "source_search.path_prefix",
                111,
                empty=True,
            )
    elif tool == "source_read":
        _require(keys == {"source_id", "start_line", "max_lines"},
                 "source_read arguments are malformed")
        _require(isinstance(arguments["source_id"], str)
                 and re.fullmatch(r"S[0-9]{4}", arguments["source_id"]) is not None,
                 "source_read source_id is malformed")
        _require(_is_int(arguments["start_line"], 1, MAX_U32),
                 "source_read start_line is malformed")
        _require(_is_int(arguments["max_lines"], 1, 12),
                 "source_read max_lines is malformed")
    elif tool == "inspect_runtime":
        _require(keys == {"operation"}
                 and arguments.get("operation") in ("system_status", "processes", "context"),
                 "inspect_runtime arguments are malformed")
    elif tool == "draft_report":
        _require({"content"} <= keys <= {"content", "title"},
                 "draft_report arguments are malformed")
        _bounded_tool_text(arguments["content"], "draft_report.content", 2800)
        if "title" in arguments:
            _bounded_tool_text(
                arguments["title"], "draft_report.title", 128, empty=True
            )
    elif tool == "read_artifact":
        _require(keys == {"handle"} and _is_int(arguments["handle"], 1, MAX_U32),
                 "read_artifact arguments are malformed")
    else:
        _fail(f"unadvertised Nexus tool: {tool}")
    return dict(arguments)


def _validate_fixture(
    records: Sequence[dict[str, object]],
    *,
    require_acceptance_scenarios: bool = True,
) -> tuple[FixtureRecord, ...]:
    _reject_stale(records, "replay fixture")
    result: list[FixtureRecord] = []
    seen: set[str] = set()
    tools: list[str] = []
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
            tools.append(str(tool))
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
        _require(finals >= 2,
                 "fixture must contain a direct final and an evidence-backed final")
        _require(frozenset(tools) == TOOL_SET,
                 "fixture must exercise exactly the five Nexus tools")
        _require(tools.count("source_search") >= 2,
                 "fixture must permit a source miss followed by a new search")
    return tuple(result)


_ANCHOR_REVISION_RE = re.compile(
    r'^#define AGENT_NEXUS_SOURCE_ANCHOR_REVISION "([0-9a-f]{64})"$', re.MULTILINE
)
_ANCHOR_MANIFEST_RE = re.compile(
    r'^#define AGENT_NEXUS_SOURCE_ANCHOR_MANIFEST_SHA256 "([0-9a-f]{64})"$', re.MULTILINE
)
_ANCHOR_SCOPE_RE = re.compile(
    r'^#define AGENT_NEXUS_SOURCE_ANCHOR_SCOPE "([^"]+)"$', re.MULTILINE
)


def _load_source_attestor(corpus: Path, anchor: Path) -> source_attestation.SourceAttestation:
    try:
        text = anchor.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise ValidationError("Nexus source anchor is unavailable") from error
    revision = _ANCHOR_REVISION_RE.findall(text)
    manifest = _ANCHOR_MANIFEST_RE.findall(text)
    scope = _ANCHOR_SCOPE_RE.findall(text)
    _require(len(revision) == len(manifest) == len(scope) == 1,
             "Nexus source anchor fields are not unique")
    _require(scope[0] == source_attestation.SCOPE,
             "Nexus source anchor scope is not build_source_snapshot")
    try:
        return source_attestation.load_source_attestation(
            corpus,
            expected_revision=revision[0],
            expected_manifest_sha256=manifest[0],
        )
    except source_attestation.SourceAttestationError as error:
        raise ValidationError(f"Nexus source corpus is not anchored: {error}") from error


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
        _require(isinstance(item, dict)
                 and set(item) == {"tool_corr_id", "tool", "projection_sha256"},
                 "model history binding fields are malformed")
        corr = item.get("tool_corr_id")
        tool = item.get("tool")
        digest = item.get("projection_sha256")
        _require(_is_int(corr, 1) and previous < int(corr) < current_corr,
                 "model history correlations are not unique and increasing")
        _require(isinstance(tool, str) and tool in TOOL_SET,
                 "model history names an unadvertised tool")
        _digest(digest, "model history projection")
        _require((int(corr), str(tool), str(digest)) in available,
                 "model history is not bound to a settled tool projection")
        normalized.append(dict(item))
        previous = int(corr)
    expected = [
        {"tool_corr_id": corr, "tool": tool, "projection_sha256": digest}
        for entry in settled[-MAX_HISTORY_BINDINGS:]
        if entry is not None
        for corr, tool, digest in (entry,)
    ]
    _require(normalized == expected,
             "model history is not the failure-aware bounded tool tail")
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
    final_evidence_root: str | None,
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
            "final_evidence_root",
            "final_response_sha256",
            "provider_proof_sha256",
        }
    else:
        _fail("model_response has an unsupported response_type")
    optional = {"endpoint_origin", "provider_response_id", *RECEIPT_FIELDS}
    _require(base_required | variant <= set(response)
             and not set(response).difference(base_required | variant | optional),
             "model_response proof fields are malformed")
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
        _require(response.get("attempt_count") == 1
                 and response.get("adaptation") == "none",
                 "DeepSeek autonomy was repaired or retried by the Host")
        raw_count = response.get("raw_tool_call_count")
        selected_index = response.get("selected_index")
        if kind == "tool_use":
            _require(raw_count == 1 and selected_index == 0,
                     "DeepSeek did not autonomously return exactly one tool call")
            selected = _sha(
                {"tool": response["tool"], "arguments": response["arguments"]}
            )
            _require(hmac.compare_digest(
                _digest(response.get("selected_tool_sha256"), "selected tool digest"),
                selected,
            ), "provider receipt does not bind the selected tool")
        else:
            _require(raw_count == 0 and selected_index == -1
                     and response.get("selected_tool_sha256") == "",
                     "DeepSeek final receipt claims a tool selection")
            selected = _sha({"type": "final", "content": response["content"]})
            _require(hmac.compare_digest(
                _digest(response.get("selected_reply_sha256"), "selected reply digest"),
                selected,
            ), "provider receipt does not bind the selected final")

    if kind == "final":
        _require(final_evidence_root is not None
                 and response.get("final_request_sha256") == request.get("request_sha256")
                 and response.get("final_response_sha256") == response_sha
                 and response.get("final_evidence_root") == final_evidence_root,
                 "final model response proof is not bound to its request/evidence")
        proof_order.extend(("final_request_sha256", "final_evidence_root"))
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
    _require(proof.get("attempt_count") == 1
             and proof.get("tool_choice_mode") == "auto"
             and proof.get("forced_tool") is None
             and _is_int(proof.get("raw_tool_call_count"), 0, 32)
             and proof.get("adaptation") == f"rejected_{str(code).lower()}",
             "DeepSeek error proof changed the autonomous adapter contract")
    if "provider_response_id" in proof:
        _bounded_text(
            proof.get("provider_response_id"),
            "provider response id",
            256,
        )


def _validate_tool_event_shape(record: Mapping[str, object]) -> None:
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
    status = int(record["status"])
    if status == 0:
        _require(record.get("provenance") == SUCCESS_PROVENANCE[record["tool"]],
                 "successful tool_event provenance is malformed")
        _digest(record.get("projection_sha256"), "successful tool projection")
    else:
        _require(record.get("provenance") == 0
                 and record.get("projection_sha256") == ""
                 and all(record.get(field) == 0 for field in ("value0", "value1", "value2")),
                 "failed tool_event exposed successful result bindings")


def _validate_evidence_event(
    record: Mapping[str, object],
    attestor: source_attestation.SourceAttestation,
) -> source_attestation.ReadAttestation:
    _require(set(record) == EVIDENCE_FIELDS, "evidence_event fields are malformed")
    _require(record.get("type") == "evidence_event"
             and record.get("version") == 1
             and record.get("event") == record.get("tool") == "source_read",
             "evidence_event identity is malformed")
    _require(record.get("scope") == source_attestation.SCOPE
             and record.get("provenance") == SUCCESS_PROVENANCE["source_read"],
             "evidence_event scope or provenance is malformed")
    _require(attestor.verify_evidence_event(record),
             "evidence_event does not replay against the anchored source corpus")
    try:
        return attestor.attest_read(
            record.get("source_id"), record.get("start_line"), record.get("end_line")
        )
    except source_attestation.SourceAttestationError as error:
        raise ValidationError("evidence_event range is not attestable") from error


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
            _require(record.get("event_type") == 2
                     and record.get("audit_kind") in (2, 3)
                     and record.get("provenance") == 0,
                     "kernel MESSAGE audit contract is malformed")
            _require(record.get("value2") == record.get("target_pid"),
                     "kernel MESSAGE audit target binding is malformed")
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
    _require(set(roles) == BUSINESS_ROLES,
             "observer did not independently authenticate all four Nexus identities")
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
    attestor: source_attestation.SourceAttestation,
    provider: str,
    model: str,
    max_rounds: int,
    global_corr: list[int],
    child_ids: set[int],
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
    evidences: dict[int, tuple[dict[str, object], source_attestation.ReadAttestation]] = {}
    evidence_bindings: dict[int, Mapping[str, object]] = {}
    settled_history: list[tuple[int, str, str] | None] = []
    successful_searches: list[tuple[int, frozenset[str]]] = []
    tool_calls: list[tuple[int, str]] = []
    latest_corr = 0
    final_response: dict[str, object] | None = None
    final_evidence_root = ""
    source_miss_corrs: list[int] = []
    source_success_corrs: list[int] = []
    source_read_corrs: list[int] = []
    runtime_corrs: list[int] = []
    drafts: dict[int, tuple[str, str, int, int]] = {}
    latest_draft_handle = 0
    report_roundtrip = False
    cancellation_started = False
    cancelled_active_worker = False
    cancelled_child_corrs: set[int] = set()
    derived_cancel_settlements: set[int] = set()
    pending_cleanup_root: dict[str, object] | None = None
    attempt_number = 0
    decision_count = 0
    retry_count = 0
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
                if role in ("research", "system"):
                    _require(record.get("artifact_handle", 0) == 0,
                             "transient source/runtime handle escaped to the controller")
                elif role == "analyst":
                    _require(_is_int(record.get("artifact_handle"), 1, MAX_U32),
                             "draft report lacks a generation-safe public handle")
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
            _require(record.get("generation") == generation
                     and record.get("request_contains_user") is True
                     and record.get("user_message_index") == 0
                     and record.get("user_content_sha256") == user_digest
                     and record.get("user_bytes") == user_bytes,
                     "model request is not bound to the current user goal")
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
                    final_evidence_root=None,
                )
            elif record.get("response_type") == "final":
                bindings: list[Mapping[str, object]] = []
                for binding in requests[corr_int]["history_bindings"]:  # type: ignore[index]
                    if binding.get("tool") != "source_read":
                        continue
                    evidence_corr = int(binding["tool_corr_id"])
                    _require(evidence_corr in evidence_bindings,
                             "final history source_read lacks Host-attested evidence")
                    bindings.append(evidence_bindings[evidence_corr])
                try:
                    final_evidence_root = source_attestation.canonical_evidence_root(bindings)
                except source_attestation.SourceAttestationError as error:
                    raise ValidationError("final evidence root cannot be reconstructed") from error
                _ledger(lambda corr_id=corr_int: ledger.freeze_provider_final(corr_id),
                        "provider final replay failed")
                _validate_provider_proof(
                    record,
                    requests[corr_int],
                    expected,
                    provider=provider,
                    model=model,
                    final_evidence_root=final_evidence_root,
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
                _ledger(
                    lambda corr_id=corr_int:
                    ledger.begin_termination(corr_id, "round_limit"),
                    "MODEL_RESPONSE decision limit replay failed",
                )

        elif kind == "evidence_event":
            corr = record.get("corr_id")
            _require(_is_int(corr, 1) and int(corr) in responses
                     and responses[int(corr)].get("tool") == "source_read"
                     and int(corr) not in evidences,
                     "evidence_event has no unique source_read call")
            replay = _validate_evidence_event(record, attestor)
            corr_int = int(corr)
            arguments = responses[corr_int]["arguments"]
            _require(record.get("source_id") == arguments.get("source_id")
                     and record.get("start_line") == arguments.get("start_line")
                     and int(record["end_line"]) <= (
                         int(arguments["start_line"]) + int(arguments["max_lines"]) - 1
                     ), "evidence_event is not bound to the model's source_read arguments")
            binding = replay.evidence_binding(
                corr_int, record.get("task_id"), record.get("provenance")
            )
            evidences[corr_int] = (dict(record), replay)
            evidence_bindings[corr_int] = binding

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
            evidence_values: dict[str, object] = {}
            if tool == "source_read" and status == 0:
                _require(corr_int in evidences,
                         "successful source_read lacks an adjacent evidence_event")
                evidence, replay = evidences[corr_int]
                _require(record.get("value1") == evidence.get("task_id")
                         and projection == evidence.get("projection_sha256")
                         == evidence.get("artifact_sha256"),
                         "source_read tool/evidence/task binding is malformed")
                _require(any(
                    search_corr < corr_int and replay.source_id in source_ids
                    for search_corr, source_ids in successful_searches
                ), "source_read did not follow discovery of its source_id")
                evidence_values = {
                    "evidence_task_id": evidence["task_id"],
                    "evidence_provenance": evidence["provenance"],
                    "evidence_artifact_sha256": evidence["artifact_sha256"],
                    "evidence_projection_sha256": evidence["projection_sha256"],
                }
                source_read_corrs.append(corr_int)
            elif corr_int in evidences:
                _fail("failed or non-source tool acquired source evidence")

            arguments = responses[corr_int]["arguments"]
            if tool == "source_search":
                try:
                    search = attestor.attest_search(
                        arguments.get("query"), arguments.get("path_prefix", "")
                    )
                except source_attestation.SourceAttestationError as error:
                    _require(
                        str(error) == "source search has no matches",
                        "source_search could not be replayed as a corpus miss",
                    )
                    miss_wrapper = {
                        "status": SOURCE_SEARCH_NOT_FOUND_STATUS,
                        "value0": 0,
                        "value1": 0,
                        "value2": 0,
                        "result": SOURCE_SEARCH_NO_MATCHES_RESULT,
                    }
                    _require(
                        status == SOURCE_SEARCH_NOT_FOUND_STATUS
                        and record.get("result") == SOURCE_SEARCH_NO_MATCHES_RESULT
                        and record.get("value0") == 0
                        and record.get("value1") == 0
                        and record.get("value2") == 0
                        and record.get("provenance") == 0
                        and record.get("projection_sha256") == ""
                        and hmac.compare_digest(
                            str(record.get("result_sha256")), _sha(miss_wrapper)
                        ),
                        "source_search corpus miss lacks its exact NOT_FOUND settlement",
                    )
                    source_miss_corrs.append(corr_int)
                else:
                    success_wrapper = {
                        "status": 0,
                        "value0": record.get("value0"),
                        "value1": record.get("value1"),
                        "value2": record.get("value2"),
                        "result": SOURCE_SEARCH_SUCCESS_RESULT,
                        "discovery_projection": search.projection,
                        "evidence_trust": "unverified_discovery_hint",
                    }
                    _require(
                        status == 0
                        and record.get("result") == SOURCE_SEARCH_SUCCESS_RESULT
                        and hmac.compare_digest(
                            projection, search.projection_sha256
                        )
                        and record.get("provenance")
                        == SUCCESS_PROVENANCE["source_search"]
                        and hmac.compare_digest(
                            str(record.get("result_sha256")),
                            _sha(success_wrapper),
                        ),
                        "source_search with Host corpus matches did not settle as the exact success projection",
                    )
                    successful_searches.append(
                        (corr_int, frozenset(match.source_id for match in search.matches))
                    )
                    source_success_corrs.append(corr_int)
            elif tool == "inspect_runtime" and status == 0:
                runtime_corrs.append(corr_int)
            elif tool == "draft_report" and status == 0:
                content = str(arguments["content"])
                content_digest = _sha_text(content)
                handle = int(record["value0"])
                _require(_is_int(handle, 1, MAX_U32)
                         and projection == content_digest,
                         "draft_report did not preserve the model-authored bytes")
                drafts[handle] = (content, content_digest, len(content.encode("utf-8")), corr_int)
                latest_draft_handle = handle
            elif tool == "read_artifact" and status == 0:
                handle = int(arguments["handle"])
                _require(handle == latest_draft_handle and handle in drafts,
                         "read_artifact did not target the latest current-turn draft")
                _content, digest, byte_count, draft_corr = drafts[handle]
                _require(draft_corr < corr_int
                         and record.get("value0") == handle
                         and record.get("value1") == byte_count
                         and record.get("value2") == task_ledger.AGENT_NEXUS_ARTIFACT_REPORT
                         and projection == digest,
                         "read_artifact did not reproduce exact draft bytes")
                report_roundtrip = True
            _ledger(
                lambda corr_id=corr_int, name=tool, event=record, extra=evidence_values:
                ledger.settle_tool(
                    corr_id,
                    tool=name,
                    status=event["status"],
                    value0=event["value0"],
                    value1=event["value1"],
                    value2=event["value2"],
                    provenance=event["provenance"],
                    projection_sha256=event["projection_sha256"],
                    result_sha256=event["result_sha256"],
                    session_blocked_marker=(
                        event["result"]
                        if event["result"] == ARTIFACT_CLEANUP_SESSION_BLOCK
                        else ""
                    ),
                    **extra,
                ),
                "TOOL_EVENT replay failed",
            )
            tool_events[corr_int] = dict(record)
            if status == 0:
                settled_history.append((corr_int, tool, projection))
            else:
                settled_history.append(None)

        elif kind == "turn_cancelling":
            _require(set(record) == {"type", "turn_id", "request_id"}
                     and not cancellation_started and latest_corr in responses,
                     "turn_cancelling is malformed or duplicated")
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
    if "context_seq" in terminal:
        _require(_is_int(terminal.get("context_seq")),
                 "TURN_COMPLETE context sequence is malformed")
    required_terminal = {
        "type", "turn_id", "request_id", "status", "final_proof_root",
        "rounds", "retries", "attempts", *FINAL_PROOF_FIELDS,
    }
    allowed_terminal = required_terminal | {"context_seq"}
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
    if terminal_status == "completed":
        _require(final_response is not None
                 and terminal.get("answer") == final_response.get("content")
                 and terminal.get("final_corr_id") == final_response.get("corr_id")
                 and terminal.get("final_request_sha256")
                 == final_response.get("final_request_sha256")
                 and terminal.get("final_response_sha256")
                 == final_response.get("final_response_sha256")
                 and terminal.get("provider_proof_sha256")
                 == final_response.get("provider_proof_sha256")
                 and terminal.get("final_evidence_root") == final_evidence_root,
                 "TURN_COMPLETE did not preserve the exact provider final proof")
        final_content = str(terminal["answer"])
    else:
        _require(final_response is None and "answer" not in terminal,
                 "non-completed turn exposed a final answer")
        final_content = ""
    final_values = {field: terminal[field] for field in FINAL_PROOF_FIELDS}
    _require(hmac.compare_digest(
        _digest(terminal.get("final_proof_root"), "final proof root"),
        _sha(final_values),
    ), "TURN_COMPLETE final proof root is malformed")

    source_read = bool(source_read_corrs)
    source_recovered = bool(
        source_miss_corrs
        and source_success_corrs
        and source_read_corrs
        and min(source_miss_corrs) < min(source_success_corrs) < min(source_read_corrs)
    )
    if source_read and terminal_status == "completed":
        final_request = requests[int(final_response["corr_id"])]  # type: ignore[index]
        final_evidence_corrs = {
            int(binding["tool_corr_id"])
            for binding in final_request["history_bindings"]  # type: ignore[index]
            if binding["tool"] == "source_read"
        }
        allowed_citations = {
            str(evidences[corr][0]["citation"]) for corr in final_evidence_corrs
        }
        cited = set(CITATION_RE.findall(final_content))
        _require(bool(cited) and cited.issubset(allowed_citations),
                 "final answer omitted its source citation or invented one")

    direct_final = bool(
        terminal_status == "completed"
        and decision_count == 1
        and not tool_calls
        and not evidences
        and snapshot.task_count == 1
    )
    return TurnReport(
        turn_id=turn_id,
        request_id=request_id,
        status=str(terminal_status),
        request_count=attempt_number,
        tool_calls=tuple(tool_calls),
        direct_final=direct_final,
        source_miss=bool(source_miss_corrs),
        source_recovered=source_recovered,
        source_read=source_read,
        runtime=bool(runtime_corrs),
        report_roundtrip=report_roundtrip,
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
    if kind == "evidence_event":
        payload = dict(record)
        payload["evidence_event"] = payload.pop("event")
        payload["event"] = "evidence_committed"
        return (_observer_projection(payload, source="guest"),)
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
    _reject_stale(observer, "observer transcript")
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
    required = ("tools", "status", "context", "tasks", "artifacts")
    for command in required:
        _require(commands.count(command) >= 1,
                 f"controller lacks a successful /{command} result")
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
                     "/tools does not expose the exact five-tool Host contract")
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
    source_attestor_value: source_attestation.SourceAttestation,
    goals: Sequence[str] | None = None,
    require_acceptance_scenarios: bool = True,
) -> ValidationSummary:
    """Validate already-decoded records.  Kept public for mutation tests."""

    _reject_stale(controller, "controller transcript")
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
    reports: list[TurnReport] = []
    previous_turn = 0
    previous_request = 0
    for index, (turn, request, records) in enumerate(slices):
        _require(turn > previous_turn and request > previous_request,
                 "turn/request ids are not session-increasing")
        report = _validate_turn(
            records,
            turn_id=turn,
            request_id=request,
            generation=generation,
            goal=(goals[index] if goals is not None else None),
            ledger=ledger,
            fixture=fixture,
            fixture_cursor=fixture_cursor,
            attestor=source_attestor_value,
            provider=str(provider),
            model=str(model),
            max_rounds=max_rounds,
            global_corr=global_corr,
            child_ids=child_ids,
        )
        reports.append(report)
        snapshot = ledger.snapshot()
        if snapshot.session_blocked:
            _require(
                index == len(slices) - 1 and report.status == "error",
                "an indeterminate Nexus session attempted another turn or non-error close",
            )
        ledger.clear()
        # Host generations fence both turn admission and terminal cleanup.
        # Cancellation installs one additional fence before TURN_COMPLETE.
        generation += 3 if report.status == "cancelled" else 2
        previous_turn, previous_request = turn, request

    _require(fixture_cursor[0] == len(fixture),
             "Guest completed before the deterministic provider fixture was exhausted")
    cleanup_session_blocked = any(
        record.get("type") == "task_event"
        and record.get("parent_task_id") == 0
        and record.get("event") == "failed"
        and record.get("status") == task_ledger.AGENT_STATUS_IO_ERROR
        and record.get("summary") == ARTIFACT_CLEANUP_SESSION_BLOCK
        for record in controller
    )
    expected_close_reason = "session_error" if cleanup_session_blocked else "guest_complete"
    _require(len([record for record in controller if record.get("type") == "session_closing"]) == 1
             and len([record for record in controller if record.get("type") == "session_closed"]) == 1
             and controller[-2].get("type") == "session_closing"
             and controller[-2].get("reason") == "user_requested"
             and controller[-1].get("type") == "session_closed"
             and controller[-1].get("reason") == expected_close_reason,
             "controller session did not close cleanly and exactly once")
    _validate_observer(observer, controller, str(ready["session_id"]))

    if require_acceptance_scenarios:
        _require(len(reports) == 2, "deterministic acceptance must use exactly two natural tasks")
        _require(reports[0].direct_final,
                 "first natural task did not prove an autonomous direct final")
        autonomous = reports[1]
        _require(autonomous.status == "completed"
                 and autonomous.source_miss
                 and autonomous.source_recovered
                 and autonomous.source_read
                 and autonomous.runtime
                 and autonomous.report_roundtrip,
                 "second natural task lacks source replan/read, runtime, or report readback")
        tools = [tool for _corr, tool in autonomous.tool_calls]
        _require(frozenset(tools) == TOOL_SET,
                 "second task did not exercise the exact five-tool public surface")
        _require(not any(report.cancelled_active_worker for report in reports),
                 "deterministic script must not overclaim active-worker cancellation")
    return ValidationSummary(len(fixture), tuple(reports), str(provider))


def validate_paths(
    *,
    controller_path: Path,
    observer_path: Path,
    fixture_path: Path,
    script_path: Path = DEFAULT_SCRIPT,
    source_corpus_path: Path = DEFAULT_CORPUS,
    source_anchor_path: Path = DEFAULT_ANCHOR,
    require_acceptance_scenarios: bool = True,
) -> ValidationSummary:
    try:
        script = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError("Nexus replay script is unavailable") from error
    goals = _validate_script_text(script)
    attestor = _load_source_attestor(source_corpus_path, source_anchor_path)
    return validate_records(
        _load_jsonl(controller_path, "controller transcript"),
        _load_jsonl(observer_path, "observer transcript"),
        _load_jsonl(fixture_path, "replay fixture"),
        source_attestor_value=attestor,
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
    parser.add_argument("--source-corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--source-anchor", type=Path, default=DEFAULT_ANCHOR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        summary = validate_paths(
            controller_path=args.controller,
            observer_path=args.observer,
            fixture_path=args.fixture,
            script_path=args.script,
            source_corpus_path=args.source_corpus,
            source_anchor_path=args.source_anchor,
        )
    except ValidationError as error:
        print(f"agentos-nexus-replay: FAIL: {error}", file=sys.stderr)
        return 1
    features = Counter(
        feature
        for report in summary.turns
        for feature, enabled in (
            ("direct-final", report.direct_final),
            ("source-replan", report.source_recovered),
            ("runtime", report.runtime),
            ("report-readback", report.report_roundtrip),
            ("active-worker-cancel", report.cancelled_active_worker),
        )
        if enabled
    )
    print(
        "agentos-nexus-replay: PASS "
        f"({summary.fixture_records} provider rounds, {len(summary.turns)} tasks, "
        f"{','.join(features)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
