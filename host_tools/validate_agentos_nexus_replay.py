#!/usr/bin/env python3
"""Validate the structured, one-boot AgentOS Nexus replay transcript."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence


MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_WIRE_U64 = (1 << 63) - 1
MAX_U32 = (1 << 32) - 1
MIN_I32 = -(1 << 31)
MAX_I32 = (1 << 31) - 1
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
AGENT_STATUS_CANCELLED = -10
NEXUS_PUBLISH_REPORT_ID = 1004
PROVENANCE_KERNEL_FACT = 1 << 0
PROVENANCE_AGENT_DERIVED = 1 << 2
PROVENANCE_UNTRUSTED_FILE_DATA = 1 << 3
PROVENANCE_UNTRUSTED_TOOL_OUTPUT = 1 << 4
PROVENANCE_CROSS_AGENT_DATA = 1 << 5
PROVENANCE_ALL = (1 << 6) - 1
BUSINESS_ROLES = frozenset(("coordinator", "system", "research", "analyst"))
TASK_EVENTS = frozenset(
    ("assigned", "accepted", "progress", "completed", "failed", "cancelled", "artifact_published")
)
TASK_STATES = frozenset(
    ("assigned", "accepted", "running", "waiting", "completed", "failed", "cancelled")
)
TASK_REQUIRED_FIELDS = frozenset(
    (
        "turn_id",
        "request_id",
        "corr_id",
        "task_id",
        "parent_task_id",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
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
TASK_OPTIONAL_FIELDS = frozenset(
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
TASK_CONTROLLER_FIELDS = (
    TASK_REQUIRED_FIELDS
    | TASK_OPTIONAL_FIELDS
    | frozenset(("type", "agent_role", "agent_control_id", "artifact_sha256"))
)
TASK_OBSERVER_FIELDS = (
    TASK_CONTROLLER_FIELDS
    - frozenset(("digest", "summary"))
    | frozenset(("source",))
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
    )
)
OBSERVER_TOOL_EVENT_FIELDS = (
    TOOL_EVENT_FIELDS
    - frozenset(("result",))
    | frozenset(("event", "source"))
)
NEXUS_PRODUCT_TOOLS = frozenset(
    ("tool_search", "delegate_task", "read_artifact", "publish_report")
)
KERNEL_AUDIT_FIELDS = frozenset(
    (
        "type",
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
KERNEL_SNAPSHOT_FIELDS = frozenset(
    (
        "type",
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
MEASUREMENT_TOKENS = (
    "source_manifest=one_shot_metrics/data/20260811/manifest.json",
    "source_table=one_shot_metrics/data/20260811/tables/contest_paired.csv",
    "records=96",
    "traversal_us=34712.5",
    "indexed_us=13293.5",
    "paired_ratio_median=3.118",
    "wins=16/16",
    "nexus_derived_checks=16/16",
)
SOURCE_REVISION_TOKEN = (
    "source_revision=2b14fb1f74b9bd093e6de939a16554620835699e"
)
OBSERVER_FORBIDDEN_FIELDS = frozenset(
    ("arguments", "canonical_arguments", "content", "objective", "raw", "result", "summary")
)


class ValidationError(ValueError):
    """A Nexus replay artifact does not satisfy the acceptance contract."""


class WorkerTaskMap(dict[int, tuple[str, int, int, int]]):
    """Worker identities plus exact controller projections visible to observers."""

    def __init__(
        self,
        controller_tasks: Sequence[dict[str, object]],
        controller_tools: Sequence[dict[str, object]],
    ) -> None:
        super().__init__()
        self.controller_tasks = tuple(controller_tasks)
        self.controller_tools = tuple(controller_tools)


def _reject_constant(value: str) -> object:
    raise ValidationError(f"non-finite JSON value is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_jsonl(path: Path, label: str) -> list[dict[str, object]]:
    try:
        size = path.stat().st_size
        if size > MAX_TRANSCRIPT_BYTES:
            raise ValidationError(f"{label} exceeds {MAX_TRANSCRIPT_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"cannot read {label}: {error}") from error

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValidationError(f"{label}:{line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValidationError(f"{label}:{line_number}: top-level JSON must be an object")
        records.append(value)
    if not records:
        raise ValidationError(f"{label} is empty")
    return records


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value: object) -> bool:
    return _is_int(value) and int(value) > 0


def _nonnegative_int(value: object) -> bool:
    return _is_int(value) and int(value) >= 0


def _wire_u64(value: object, *, positive: bool = False) -> bool:
    minimum = 1 if positive else 0
    return _is_int(value) and minimum <= int(value) <= MAX_WIRE_U64


def _u32(value: object) -> bool:
    return _is_int(value) and 0 <= int(value) <= MAX_U32


def _i32(value: object, *, positive: bool = False) -> bool:
    minimum = 1 if positive else MIN_I32
    return _is_int(value) and minimum <= int(value) <= MAX_I32


def _bounded_text(value: object, maximum: int, *, empty: bool = True) -> bool:
    if not isinstance(value, str) or (not empty and not value):
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= maximum and not any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _field_tokens(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {
        token.strip()
        for token in re.split(r"[;\n]", value)
        if token.strip()
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _select(
    records: Sequence[dict[str, object]], key: str, value: object
) -> list[dict[str, object]]:
    return [record for record in records if record.get(key) == value]


def _status_is(record: Mapping[str, object], expected: int) -> bool:
    value = record.get("status")
    return _is_int(value) and int(value) == expected


def _fixture_digests(records: Sequence[dict[str, object]]) -> list[str]:
    digests: list[str] = []
    final_count = 0
    final_texts: list[str] = []
    tools: list[str] = []
    turns: list[list[dict[str, object]]] = []
    current_turn: list[dict[str, object]] = []
    for index, record in enumerate(records, 1):
        _require(
            set(record) == {"request_sha256", "response"},
            f"fixture response {index} has unexpected fields",
        )
        digest = record.get("request_sha256")
        _require(
            isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
            f"fixture response {index} has no valid request_sha256",
        )
        response = record.get("response")
        _require(isinstance(response, dict), f"fixture response {index} is malformed")
        kind = response.get("type")
        _require(kind in ("tool_use", "final"), f"fixture response {index} has invalid type")
        if kind == "final":
            _require(
                set(response) == {"type", "content"},
                f"fixture final response {index} has unexpected fields",
            )
            final_count += 1
            content = response.get("content")
            _require(
                isinstance(content, str) and bool(content.strip()),
                f"fixture response {index} has no final content",
            )
            final_texts.append(content)
            turns.append(current_turn)
            current_turn = []
        else:
            _require(
                set(response) == {"type", "tool", "arguments"},
                f"fixture tool response {index} has unexpected fields",
            )
            tool = response.get("tool")
            _require(isinstance(tool, str) and bool(tool), f"fixture response {index} has no tool")
            _require(isinstance(response.get("arguments"), dict), f"fixture response {index} has no arguments")
            tools.append(tool)
            current_turn.append(response)
        digests.append(digest)
    _require(len(set(digests)) == len(digests), "fixture request_sha256 values must be unique")
    _require(final_count == 3, "fixture must terminate exactly three user turns")
    safe_final = final_texts[-1].lower()
    _require(
        any(token in safe_final for token in ("denied", "unpublished", "not published")),
        "fixture final answer does not acknowledge the denied publication",
    )
    _require(
        not any(token in safe_final for token in ("published successfully", "publication complete")),
        "fixture final answer falsely claims publication",
    )
    for tool in ("tool_search", "delegate_task", "read_artifact", "publish_report"):
        _require(tool in tools, f"fixture does not exercise {tool}")
    _require(not current_turn and len(turns) == 3, "fixture tool calls are not bounded by three finals")

    def calls(turn: int, tool: str) -> list[dict[str, object]]:
        return [response for response in turns[turn - 1] if response.get("tool") == tool]

    def arguments(response: Mapping[str, object]) -> Mapping[str, object]:
        value = response.get("arguments")
        _require(isinstance(value, dict), "fixture tool call lost its argument object")
        return value

    def valid_handle(value: object) -> bool:
        return (
            _positive_int(value)
            and int(value) <= 0xFFFFFFFF
            and ((int(value) >> 16) & 0xFFFF) > 0
            and 0 < (int(value) & 0xFFFF) <= 32
        )

    system_searches = calls(1, "tool_search")
    system_delegates = calls(1, "delegate_task")
    _require(
        any(arguments(call).get("role") == "system" for call in system_searches),
        "turn 1 does not discover System tools",
    )
    _require(
        any(
            arguments(call).get("role") == "system"
            and arguments(call).get("task_type") == "system_snapshot"
            for call in system_delegates
        ),
        "turn 1 does not delegate a System snapshot",
    )

    research_delegates = [
        call
        for call in calls(2, "delegate_task")
        if arguments(call).get("role") == "research"
        and arguments(call).get("task_type") == "local_research"
    ]
    _require(len(research_delegates) >= 2, "turn 2 lacks the failed Research attempt and replan")
    research_handles = [arguments(call).get("input_handle") for call in research_delegates]
    _require(not valid_handle(research_handles[0]), "turn 2 first Research source is not deliberately invalid")
    _require(
        any(valid_handle(handle) for handle in research_handles[1:]),
        "turn 2 never replans with a generation-safe local source handle",
    )
    _require(calls(2, "read_artifact"), "turn 2 does not read back the Research artifact")

    analyst_delegates = [
        call
        for call in calls(3, "delegate_task")
        if arguments(call).get("role") == "analyst"
        and arguments(call).get("task_type") == "compose_report"
    ]
    _require(analyst_delegates, "turn 3 does not delegate the Analyst report")
    analyst_arguments = arguments(analyst_delegates[-1])
    _require(
        valid_handle(analyst_arguments.get("input_handle"))
        and valid_handle(analyst_arguments.get("secondary_handle"))
        and analyst_arguments.get("input_handle") != analyst_arguments.get("secondary_handle"),
        "turn 3 Analyst does not consume two distinct generation-safe artifacts",
    )
    report_reads = calls(3, "read_artifact")
    publishes = calls(3, "publish_report")
    _require(report_reads and publishes, "turn 3 does not read and request publication of the report")
    report_handle = arguments(report_reads[-1]).get("handle")
    _require(valid_handle(report_handle), "turn 3 report read has an invalid handle")
    _require(
        arguments(publishes[-1]).get("handle") == report_handle,
        "turn 3 publishes a different handle than the report it read",
    )
    return digests


def _fixture_turns(
    records: Sequence[dict[str, object]],
) -> list[list[Mapping[str, object]]]:
    turns: list[list[Mapping[str, object]]] = []
    current: list[Mapping[str, object]] = []
    for record in records:
        response = record.get("response")
        _require(isinstance(response, dict), "validated fixture response disappeared")
        if response.get("type") == "final":
            turns.append(current)
            current = []
        else:
            current.append(response)
    _require(not current and len(turns) == 3, "validated fixture turn partition changed")
    return turns


def _fixture_arguments(response: Mapping[str, object]) -> Mapping[str, object]:
    value = response.get("arguments")
    _require(isinstance(value, dict), "validated fixture arguments disappeared")
    return value


def _validate_fixture_artifact_flow(
    fixture: Sequence[dict[str, object]],
    system_handle: int,
    research_handle: int,
    analyst_handle: int,
) -> None:
    turns = _fixture_turns(fixture)
    lifecycle_generation = (system_handle >> 16) & 0xFFFF
    for turn in turns:
        for response in turn:
            arguments = response.get("arguments")
            if not isinstance(arguments, Mapping):
                continue
            for key in ("handle", "input_handle", "secondary_handle"):
                handle = arguments.get(key)
                if not _positive_int(handle) or int(handle) > 0xFFFFFFFF:
                    continue
                generation = (int(handle) >> 16) & 0xFFFF
                slot = int(handle) & 0xFFFF
                if generation == 0 or not 0 < slot <= 32:
                    continue
                _require(
                    generation == lifecycle_generation,
                    f"fixture {key} crossed the workflow lifecycle generation",
                )

    research_reads = [
        response for response in turns[1] if response.get("tool") == "read_artifact"
    ]
    _require(
        any(_fixture_arguments(response).get("handle") == research_handle for response in research_reads),
        "fixture does not feed the returned Research artifact back to the model",
    )

    analyst_delegates = [
        response
        for response in turns[2]
        if response.get("tool") == "delegate_task"
        and _fixture_arguments(response).get("role") == "analyst"
        and _fixture_arguments(response).get("task_type") == "compose_report"
    ]
    _require(analyst_delegates, "fixture has no Analyst report delegation")
    analyst_arguments = _fixture_arguments(analyst_delegates[-1])
    _require(
        analyst_arguments.get("input_handle") == system_handle
        and analyst_arguments.get("secondary_handle") == research_handle,
        "fixture Analyst inputs do not match the returned System and Research artifacts",
    )

    report_reads = [
        response for response in turns[2] if response.get("tool") == "read_artifact"
    ]
    publishes = [
        response for response in turns[2] if response.get("tool") == "publish_report"
    ]
    _require(
        any(_fixture_arguments(response).get("handle") == analyst_handle for response in report_reads),
        "fixture does not read the returned Analyst report artifact",
    )
    _require(
        len(publishes) == 1
        and _fixture_arguments(publishes[0]).get("handle") == analyst_handle,
        "fixture publication request is not bound to the returned Analyst report",
    )


def _validate_controls(records: Sequence[dict[str, object]]) -> None:
    controls = _select(records, "type", "control_result")
    minimum = {"tools": 1, "status": 2, "context": 3}
    for command, count in minimum.items():
        matches = _select(controls, "command", command)
        _require(len(matches) >= count, f"expected at least {count} successful /{command} results")
        _require(
            all(record.get("status") == "ok" for record in matches),
            f"/{command} did not always return status=ok",
        )
    status_results = [
        record.get("result")
        for record in _select(controls, "command", "status")
        if isinstance(record.get("result"), dict)
    ]
    _require(status_results, "/status exposed no Guest runtime result")
    _require(
        any(_positive_int(result.get("capability_mask")) for result in status_results),
        "/status exposed no nonzero kernel capability mask",
    )
    context_results = [
        record.get("result")
        for record in _select(controls, "command", "context")
        if isinstance(record.get("result"), dict)
    ]
    _require(context_results, "/context exposed no Guest Context result")
    _require(
        any(_positive_int(result.get("provenance")) for result in context_results),
        "/context exposed no nonzero provenance labels",
    )


def _validate_task_shape(
    record: Mapping[str, object], label: str, *, observer: bool = False
) -> None:
    allowed = TASK_OBSERVER_FIELDS if observer else TASK_CONTROLLER_FIELDS
    missing = TASK_REQUIRED_FIELDS.difference(record)
    _require(not missing, f"{label} lacks fields: {','.join(sorted(missing))}")
    unknown = set(record).difference(allowed)
    _require(not unknown, f"{label} has unexpected fields: {','.join(sorted(unknown))}")
    _require(
        record.get("type") == ("telemetry" if observer else "task_event"),
        f"{label} has an invalid record type",
    )
    if observer:
        _require(record.get("source") == "guest", f"{label} is not Guest-origin telemetry")
    for key in (
        "turn_id",
        "request_id",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
    ):
        _require(_wire_u64(record.get(key), positive=True), f"{label} has invalid {key}")
    _require(
        _u32(record.get("task_id")) and int(record["task_id"]) > 0,
        f"{label} has invalid task_id",
    )
    _require(_wire_u64(record.get("corr_id")), f"{label} has invalid corr_id")
    _require(_u32(record.get("parent_task_id")), f"{label} has invalid parent_task_id")
    _require(_wire_u64(record.get("tick")), f"{label} has invalid tick")
    for key in ("agent_pid", "agent_id"):
        _require(_i32(record.get(key), positive=True), f"{label} has invalid {key}")
    _require(_i32(record.get("status")), f"{label} has invalid status")
    _require(record.get("event") in TASK_EVENTS, f"{label} has unsupported event")
    _require(record.get("task_state") in TASK_STATES, f"{label} has unsupported task_state")
    expected_states = {
        "assigned": frozenset(("assigned",)),
        "accepted": frozenset(("accepted",)),
        "progress": frozenset(("running", "waiting")),
        "artifact_published": frozenset(("running", "completed")),
        "completed": frozenset(("completed",)),
        "failed": frozenset(("failed",)),
        "cancelled": frozenset(("cancelled",)),
    }
    _require(
        record.get("task_state") in expected_states[str(record["event"])],
        f"{label} event/state transition is invalid",
    )
    role = record.get("role")
    _require(role in BUSINESS_ROLES or role == "relay", f"{label} has unsupported role")
    _require(record.get("agent_role") == role, f"{label} changed its Agent role alias")
    known = record.get("control_id_known")
    _require(isinstance(known, bool), f"{label} has invalid control_id_known")
    control = record.get("control_id")
    if known:
        _require(_wire_u64(control, positive=True), f"{label} omits its known control_id")
        _require(
            record.get("agent_control_id") == control,
            f"{label} changed its control identity alias",
        )
    else:
        _require(control is None, f"{label} exposes an unknown control_id")
        _require(
            "agent_control_id" not in record,
            f"{label} exposes an alias for an unknown control identity",
        )
    for key in ("deadline_tick", "artifact_handle", "metric_code", "metric_value"):
        if key in record:
            _require(_u32(record.get(key)), f"{label} has invalid {key}")
    for key in ("context_seq", "provenance", "resource_used"):
        if key in record:
            _require(_wire_u64(record.get(key)), f"{label} has invalid {key}")
    for key in ("source_pid", "target_pid"):
        if key in record:
            _require(_i32(record.get(key), positive=True), f"{label} has invalid {key}")
    digest = record.get("artifact_sha256", record.get("digest"))
    if digest is not None:
        _require(
            isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
            f"{label} has invalid artifact digest",
        )
    if observer:
        _require("digest" not in record, f"{label} leaks the Guest digest wire field")
    elif "digest" in record:
        _require(
            record.get("artifact_sha256") == record.get("digest"),
            f"{label} changed its artifact digest alias",
        )
    else:
        _require(
            "artifact_sha256" not in record,
            f"{label} exposes an artifact digest alias without a wire digest",
        )
    summary = record.get("summary")
    if summary is not None:
        _require(
            _bounded_text(summary, 256),
            f"{label} has an invalid task summary",
        )


def _validate_tool_shape(
    record: Mapping[str, object], label: str, *, observer: bool = False
) -> None:
    allowed = OBSERVER_TOOL_EVENT_FIELDS if observer else TOOL_EVENT_FIELDS
    _require(set(record) == set(allowed), f"{label} fields do not match the Nexus TOOL_EVENT schema")
    _require(
        record.get("type") == ("telemetry" if observer else "tool_event"),
        f"{label} has an invalid record type",
    )
    if observer:
        _require(record.get("event") == "tool_event", f"{label} has an invalid event")
        _require(record.get("source") == "guest", f"{label} is not Guest-origin telemetry")
    for key in ("turn_id", "request_id", "corr_id"):
        _require(_wire_u64(record.get(key), positive=True), f"{label} has invalid {key}")
    tool = record.get("tool")
    _require(
        tool in NEXUS_PRODUCT_TOOLS and _bounded_text(tool, 64, empty=False),
        f"{label} has an unsupported tool",
    )
    _require(_i32(record.get("status")), f"{label} has invalid status")
    _require(
        _wire_u64(record.get("sequence")) and record.get("sequence") == 0,
        f"{label} pseudo tool must have sequence=0",
    )
    for key in ("value0", "value1", "value2", "context_seq", "provenance"):
        _require(_wire_u64(record.get(key)), f"{label} has invalid {key}")
    provenance = int(record["provenance"])
    _require(
        provenance & ~PROVENANCE_ALL == 0,
        f"{label} has unknown provenance labels",
    )
    if _status_is(record, 0):
        _require(_positive_int(record.get("context_seq")), f"{label} has no Context evidence")
        _require(provenance != 0, f"{label} has no provenance evidence")
    if observer:
        _require("result" not in record, f"{label} leaks a tool result")
    else:
        _require(
            _bounded_text(record.get("result"), 256, empty=False),
            f"{label} has an invalid bounded result",
        )


def _validate_identities(task_events: Sequence[dict[str, object]]) -> None:
    identities: dict[tuple[int, int, int], set[str]] = defaultdict(set)
    roles_seen: set[str] = set()
    for record in task_events:
        role = record.get("role")
        if role not in BUSINESS_ROLES or record.get("control_id_known") is not True:
            continue
        identity = (
            int(record["agent_pid"]),
            int(record["agent_id"]),
            int(record["control_id"]),
        )
        identities[identity].add(str(role))
        roles_seen.add(str(role))
    _require(roles_seen == BUSINESS_ROLES, "Nexus did not expose all four business Agent identities")
    _require(len(identities) >= 4, "Nexus did not use at least four independent business Agents")
    _require(
        all(len(roles) == 1 for roles in identities.values()),
        "one Agent identity was reused across different business roles",
    )
    pids = {identity[0] for identity in identities}
    agent_ids = {identity[1] for identity in identities}
    control_ids = {identity[2] for identity in identities}
    _require(len(pids) >= 4, "business roles do not have independent PIDs")
    _require(len(agent_ids) >= 4, "business roles do not have independent Agent IDs")
    _require(len(control_ids) >= 4, "business roles do not have independent control IDs")


def _validate_task_dag(task_events: Sequence[dict[str, object]]) -> None:
    lifecycles = {
        (
            int(record["workflow_lifecycle_id"]),
            int(record["workflow_lifecycle_generation"]),
        )
        for record in task_events
    }
    _require(len(lifecycles) == 1, "TASK events changed workflow lifecycle")
    grouped: dict[int, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    for index, record in enumerate(task_events):
        grouped[int(record["task_id"])].append((index, record))

    parents: dict[int, int] = {}
    for task_id, events in grouped.items():
        identities = {
            (
                record.get("role"),
                record.get("agent_pid"),
                record.get("agent_id"),
                record.get("control_id_known"),
                record.get("control_id"),
            )
            for _, record in events
        }
        _require(len(identities) == 1, f"task {task_id} changed Agent identity")
        values = {int(record["parent_task_id"]) for _, record in events}
        _require(len(values) == 1, f"task {task_id} changed parent_task_id")
        parent = values.pop()
        _require(parent != task_id, f"task {task_id} is its own parent")
        parents[task_id] = parent
        if parent != 0:
            _require(
                all("deadline_tick" in record for _, record in events),
                f"task {task_id} lacks its delegated-task deadline",
            )
            deadlines = {
                int(record["deadline_tick"])
                for _, record in events
            }
            _require(
                len(deadlines) == 1,
                f"task {task_id} changed its delegated-task deadline",
            )
            deadline = next(iter(deadlines))
            _require(deadline > 0, f"task {task_id} has an invalid deadline")
            _require(
                all(deadline > int(record["tick"]) for _, record in events),
                f"task {task_id} deadline is not in the future",
            )
        turn_requests = {
            (int(record["turn_id"]), int(record["request_id"]))
            for _, record in events
        }
        _require(
            len(turn_requests) == 1,
            f"task {task_id} changed its active turn/request envelope",
        )
        if parent != 0:
            envelopes = {
                (
                    int(record["turn_id"]),
                    int(record["request_id"]),
                    int(record["corr_id"]),
                )
                for _, record in events
            }
            _require(
                len(envelopes) == 1,
                f"delegated task {task_id} changed its model response envelope",
            )
        kinds = [str(record["event"]) for _, record in events]
        _require(
            kinds.count("assigned") == 1,
            f"task {task_id} lacks exactly one TASK_ASSIGN",
        )
        assigned = kinds.index("assigned")
        _require(assigned == 0, f"task {task_id} emitted an event before assignment")
        _require(
            kinds.count("accepted") == 1,
            f"task {task_id} lacks exactly one TASK_ACCEPT",
        )
        accepted = kinds.index("accepted")
        _require(assigned < accepted, f"task {task_id} accepted before assignment")
        progress_positions = [
            index for index, kind in enumerate(kinds) if kind == "progress"
        ]
        _require(progress_positions, f"task {task_id} lacks TASK_PROGRESS")
        _require(
            accepted < progress_positions[0],
            f"task {task_id} progressed before acceptance",
        )
        terminals = [
            (index, kind)
            for index, kind in enumerate(kinds)
            if kind in ("completed", "failed", "cancelled")
        ]
        _require(len(terminals) == 1, f"task {task_id} lacks exactly one terminal TASK_RESULT")
        terminal_index, terminal = terminals[0]
        terminal_status = events[terminal_index][1].get("status")
        if terminal == "completed":
            _require(terminal_status == 0, f"task {task_id} completed with nonzero status")
        elif terminal == "failed":
            _require(terminal_status != 0, f"task {task_id} failed with success status")
        else:
            _require(
                terminal_status == AGENT_STATUS_CANCELLED,
                f"task {task_id} cancelled without AGENT_STATUS_CANCELLED",
            )
        _require(assigned < terminal_index, f"task {task_id} terminated before assignment")
        _require(
            all(position < terminal_index for position in progress_positions),
            f"task {task_id} progressed after its terminal state",
        )
        transitions_after_terminal = [
            kind for kind in kinds[terminal_index + 1 :] if kind != "artifact_published"
        ]
        _require(
            not transitions_after_terminal,
            f"task {task_id} emitted a state transition after its terminal state",
        )
        artifact_positions = [
            index for index, kind in enumerate(kinds) if kind == "artifact_published"
        ]
        _require(
            len(artifact_positions) <= 1,
            f"task {task_id} published more than one artifact",
        )
        if artifact_positions:
            _require(
                terminal == "completed",
                f"task {task_id} published an artifact after an unsuccessful terminal state",
            )
            _require(
                artifact_positions[0] > terminal_index,
                f"task {task_id} claimed publication before the worker terminal result",
            )
            _require(
                events[artifact_positions[0]][1].get("task_state") == "completed",
                f"task {task_id} post-terminal artifact is not in completed state",
            )
            _require(
                events[artifact_positions[0]][1].get("status") == 0,
                f"task {task_id} post-terminal artifact has nonzero status",
            )

    assigned_positions = {
        task_id: min(index for index, record in events if record.get("event") == "assigned")
        for task_id, events in grouped.items()
    }
    for task_id, parent in parents.items():
        _require(parent == 0 or parent in grouped, f"task {task_id} references an unknown parent")
        if parent != 0 and parent in grouped:
            child_first = min(index for index, _ in grouped[task_id])
            _require(
                assigned_positions[parent] < child_first,
                f"task {task_id} began before parent {parent} assignment",
            )
            parent_terminal = next(
                index
                for index, record in grouped[parent]
                if record.get("event") in ("completed", "failed", "cancelled")
            )
            child_last = max(index for index, _ in grouped[task_id])
            _require(
                child_last < parent_terminal,
                f"task {task_id} outlived terminal parent {parent}",
            )
            parent_assigned = next(
                record
                for _, record in grouped[parent]
                if record.get("event") == "assigned"
            )
            child_assigned = next(
                record
                for _, record in grouped[task_id]
                if record.get("event") == "assigned"
            )
            _require(
                (parent_assigned.get("turn_id"), parent_assigned.get("request_id"))
                == (child_assigned.get("turn_id"), child_assigned.get("request_id")),
                f"task {task_id} changed its parent turn/request envelope",
            )
        seen = {task_id}
        cursor = parent
        while cursor:
            _require(cursor not in seen, "task parent graph contains a cycle")
            seen.add(cursor)
            cursor = parents.get(cursor, 0)
    _require(any(parent != 0 for parent in parents.values()), "Nexus transcript has no delegated task edge")

    def terminal(role: str, event: str, turn_id: int) -> list[tuple[int, int]]:
        return [
            (index, int(record["task_id"]))
            for index, record in enumerate(task_events)
            if record.get("role") == role
            and record.get("event") == event
            and record.get("turn_id") == turn_id
        ]

    system_done = terminal("system", "completed", 1)
    research_failed = terminal("research", "failed", 2)
    research_done = terminal("research", "completed", 2)
    analyst_done = terminal("analyst", "completed", 3)
    _require(system_done, "turn 1 has no completed System delegation")
    _require(research_failed, "turn 2 has no failed Research attempt")
    _require(research_done, "turn 2 has no successful Research replan")
    _require(analyst_done, "turn 3 has no completed Analyst delegation")
    failed_index, failed_id = research_failed[0]
    completed_index, completed_id = research_done[-1]
    _require(failed_id != completed_id, "Research replan reused the failed task identity")
    _require(failed_index < completed_index, "Research succeeded before its required failed attempt")


def _task_artifacts(
    task_events: Sequence[dict[str, object]], role: str, turn_id: int
) -> list[dict[str, object]]:
    return [
        record
        for record in task_events
        if record.get("event") == "artifact_published"
        and record.get("role") == role
        and record.get("turn_id") == turn_id
    ]


def _validate_artifacts(
    task_events: Sequence[dict[str, object]],
) -> tuple[
    int,
    int,
    int,
    str,
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
]:
    system = _task_artifacts(task_events, "system", 1)
    research = _task_artifacts(task_events, "research", 2)
    analyst = _task_artifacts(task_events, "analyst", 3)
    _require(system, "System task did not produce an artifact")
    _require(research, "successful Research task did not produce an artifact")
    _require(analyst, "Analyst task did not produce a report artifact")
    selected = (system[-1], research[-1], analyst[-1])
    handles: list[int] = []
    digests: list[str] = []
    provenances: list[int] = []
    for label, record in zip(("System", "Research", "Analyst"), selected):
        handle = record.get("artifact_handle")
        digest = record.get("artifact_sha256", record.get("digest"))
        _require(_positive_int(handle), f"{label} artifact has no generation-safe handle")
        _require(int(handle) <= 0xFFFFFFFF, f"{label} artifact handle exceeds uint32")
        generation = (int(handle) >> 16) & 0xFFFF
        slot = int(handle) & 0xFFFF
        _require(
            generation > 0 and 0 < slot <= 32,
            f"{label} artifact handle has an invalid generation or slot",
        )
        _require(
            isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
            f"{label} artifact has no SHA-256",
        )
        _require(
            _positive_int(record.get("provenance")),
            f"{label} artifact has no nonzero provenance evidence",
        )
        _require(
            int(record["provenance"]) & ~PROVENANCE_ALL == 0,
            f"{label} artifact has unknown provenance labels",
        )
        _require(
            _positive_int(record.get("resource_used")),
            f"{label} artifact has no positive resource-account evidence",
        )
        handles.append(int(handle))
        digests.append(str(digest))
        provenances.append(int(record["provenance"]))
    _require(len(set(handles)) == 3, "Nexus artifact handles are not distinct")
    _require(len(set(digests)) == 3, "Nexus artifact SHA-256 values are not distinct")
    generations = [(handle >> 16) & 0xFFFF for handle in handles]
    slots = [handle & 0xFFFF for handle in handles]
    lifecycle_generation = int(selected[0]["workflow_lifecycle_generation"])
    _require(
        0 < lifecycle_generation <= 0xFFFF,
        "Nexus lifecycle generation does not fit the artifact handle ABI",
    )
    _require(
        set(generations) == {lifecycle_generation},
        "Nexus artifact handle generation does not match the workflow lifecycle",
    )
    _require(
        slots == sorted(slots) and len(set(slots)) == len(slots),
        "Nexus artifact slots were aliased or reused out of allocation order",
    )
    worker_required = (
        PROVENANCE_AGENT_DERIVED
        | PROVENANCE_UNTRUSTED_TOOL_OUTPUT
        | PROVENANCE_CROSS_AGENT_DATA
    )
    system_required = worker_required | PROVENANCE_KERNEL_FACT
    _require(
        provenances[0] & system_required == system_required,
        "System artifact lost kernel/model-derived/tool/cross-Agent provenance",
    )
    research_required = (
        PROVENANCE_UNTRUSTED_FILE_DATA | worker_required
    )
    _require(
        provenances[1] & research_required == research_required,
        "Research artifact lost file/tool/cross-Agent provenance",
    )
    analyst_required = (
        provenances[0]
        | provenances[1]
        | PROVENANCE_AGENT_DERIVED
        | PROVENANCE_UNTRUSTED_TOOL_OUTPUT
        | PROVENANCE_CROSS_AGENT_DATA
    )
    _require(
        provenances[2] & analyst_required == analyst_required,
        "Analyst report does not preserve both input provenance sets",
    )

    system_summary = "\n".join(str(record.get("summary", "")) for record in system)
    system_facts = ["source=nexus_state", "claim=this_boot_runtime_observation"]
    system_tokens = _field_tokens(system_summary)
    for token in system_facts:
        _require(token in system_tokens, f"System artifact omits {token}")
    for key in ("process_count", "context_count", "file_bytes"):
        match = re.search(rf"(?:^|[;\n]){key}=([0-9]+)(?:$|[;\n])", system_summary)
        _require(
            match is not None and int(match.group(1)) > 0,
            f"System artifact has no positive dynamic {key}",
        )
        system_facts.append(f"{key}={match.group(1)}")
    for key in ("sched_dispatch_count", "sched_budget_used", "sched_vruntime"):
        match = re.search(rf"(?:^|[;\n]){key}=([0-9]+)(?:$|[;\n])", system_summary)
        _require(
            match is not None and int(match.group(1)) > 0,
            f"System artifact has no positive this-boot {key}",
        )
    budget_match = re.search(
        r"(?:^|[;\n])sched_budget=([0-9]+)(?:$|[;\n])", system_summary
    )
    _require(
        budget_match is not None and int(budget_match.group(1)) > 0,
        "System artifact has no positive this-boot sched_budget",
    )
    stable_scheduler_fact = f"sched_budget={budget_match.group(1)}"

    research_summary = "\n".join(
        str(record.get("summary", "")) for record in research
    )
    research_tokens = _field_tokens(research_summary)
    for token in MEASUREMENT_TOKENS:
        _require(token in research_tokens, f"Research artifact omits {token}")
    analyst_summary = "\n".join(str(record.get("summary", "")) for record in analyst)
    analyst_tokens = _field_tokens(analyst_summary)
    _require(
        f"system_handle={handles[0]}" in analyst_tokens,
        "Analyst report does not cite the System artifact handle",
    )
    _require(
        f"research_handle={handles[1]}" in analyst_tokens,
        "Analyst report does not cite the Research artifact handle",
    )
    _require(
        f"system_digest={digests[0]}" in analyst_tokens,
        "Analyst report does not preserve the verified System digest",
    )
    _require(
        f"research_digest={digests[1]}" in analyst_tokens,
        "Analyst report does not preserve the verified Research digest",
    )
    _require(
        "paired_ratio_median=3.118" in analyst_tokens,
        "Analyst report summary contains no verified paired measurement",
    )
    _require(
        stable_scheduler_fact in analyst_tokens,
        "Analyst report summary does not preserve the verified scheduler budget",
    )
    return (
        handles[0],
        handles[1],
        handles[2],
        digests[0],
        digests[1],
        digests[2],
        tuple(system_facts),
        (stable_scheduler_fact,),
    )


def _validate_approval_and_tools(
    records: Sequence[dict[str, object]],
    research_handle: int,
    analyst_handle: int,
    response_by_corr: Mapping[int, dict[str, object]],
) -> None:
    tools = _select(records, "type", "tool_event")
    for name in ("tool_search", "delegate_task", "read_artifact"):
        events = _select(tools, "tool", name)
        _require(events, f"Nexus did not call {name}")
        _require(any(_status_is(record, 0) for record in events), f"{name} never succeeded")
        _require(
            any(
                _status_is(record, 0) and _positive_int(record.get("provenance"))
                for record in events
            ),
            f"{name} has no successful result with provenance evidence",
        )
    delegates = _select(tools, "tool", "delegate_task")
    _require(len(delegates) >= 4, "Nexus did not perform the required dynamic delegations and replan")
    reads = [record for record in _select(tools, "tool", "read_artifact") if _status_is(record, 0)]
    _require(len(reads) >= 2, "Research and Analyst artifacts were not both read back")
    _require(
        any(record.get("value0") == research_handle for record in reads),
        "the successful Research artifact was not read back by handle",
    )
    _require(
        any(record.get("value0") == analyst_handle for record in reads),
        "the composed Analyst artifact was not read back by handle",
    )

    requests = _select(records, "type", "approval_request")
    _require(
        len(requests) == 1 and requests[0].get("tool") == "publish_report",
        "expected exactly one publish_report approval request",
    )
    approval = requests[0]
    expected_arguments = {"handle": analyst_handle}
    expected_canonical = _canonical_json(expected_arguments)
    expected_digest = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
    _require(
        approval.get("tool_id") == NEXUS_PUBLISH_REPORT_ID,
        "publish_report approval has the wrong tool_id",
    )
    _require(
        approval.get("arguments") == expected_arguments,
        "publish_report approval is not bound to the Analyst handle",
    )
    _require(
        approval.get("canonical_arguments") == expected_canonical,
        "publish_report approval has noncanonical arguments",
    )
    _require(
        approval.get("arguments_sha256") == expected_digest,
        "publish_report approval argument digest was not recomputed",
    )
    _require(_positive_int(approval.get("corr_id")), "approval has invalid corr_id")
    response = response_by_corr.get(int(approval["corr_id"]))
    _require(
        response is not None
        and response.get("response_type") == "tool_use"
        and response.get("tool") == "publish_report"
        and response.get("arguments") == expected_arguments
        and _positive_int(approval.get("turn_id"))
        and _positive_int(approval.get("request_id"))
        and (approval.get("turn_id"), approval.get("request_id"))
        == (response.get("turn_id"), response.get("request_id")),
        "publish_report approval does not match its model response",
    )
    positions = {id(record): index for index, record in enumerate(records)}
    approval_position = positions[id(approval)]
    prior_responses = [
        record
        for record in _select(records, "type", "model_response")
        if positions[id(record)] < approval_position
    ]
    _require(
        prior_responses and prior_responses[-1] is response,
        "publish_report approval is not bound to the latest model response",
    )
    decisions = _select(records, "type", "approval_decision")
    _require(len(decisions) == 1, "expected exactly one approval decision")
    _require(decisions[0].get("decision") == "deny", "publish_report must be denied in replay")
    for key in (
        "turn_id",
        "request_id",
        "corr_id",
        "tool",
        "arguments_sha256",
        "nonce",
        "tool_id",
        "issued_tick",
        "expires_tick",
    ):
        _require(decisions[0].get(key) == approval.get(key), f"approval decision is not bound by {key}")
    _require(_nonnegative_int(approval.get("issued_tick")), "approval has invalid issued_tick")
    _require(_positive_int(approval.get("expires_tick")), "approval has invalid expires_tick")
    _require(
        int(approval["expires_tick"]) > int(approval["issued_tick"]),
        "approval expiry is not later than issuance",
    )
    _require(
        isinstance(approval.get("arguments_sha256"), str)
        and DIGEST_RE.fullmatch(str(approval["arguments_sha256"])) is not None,
        "approval has an invalid argument digest",
    )
    _require(
        isinstance(approval.get("nonce"), str) and bool(approval["nonce"]),
        "approval has no nonce",
    )

    publishes = _select(tools, "tool", "publish_report")
    _require(len(publishes) == 1, "publish_report must produce exactly one denied tool result")
    denied = publishes[0]
    _require(_status_is(denied, -8), "publish_report denial must have status=-8")
    _require(denied.get("result") == "not_approved", "publish_report denial is not not_approved")
    _require(denied.get("corr_id") == approval.get("corr_id"), "denial corr_id is not approval-bound")
    for key in ("value0", "value1", "value2"):
        if key in denied:
            _require(denied[key] == 0, f"denied publish_report reports a nonzero {key} effect")

    denied_corr = denied.get("corr_id")
    leaked = [
        record
        for record in _select(records, "type", "task_event")
        if record.get("event") == "artifact_published"
        and record.get("corr_id") == denied_corr
    ]
    _require(not leaked, "denied publish_report produced an artifact side effect")


def _validate_context_alignment(
    task_events: Sequence[dict[str, object]], requests: Sequence[dict[str, object]]
) -> None:
    correlations: dict[int, tuple[int, int]] = {}
    for request in requests:
        corr = request.get("corr_id")
        _require(_positive_int(corr), "model_request has invalid corr_id")
        pair = (int(request["turn_id"]), int(request["request_id"]))
        _require(int(corr) not in correlations, "model_request corr_id is duplicated")
        correlations[int(corr)] = pair
    _require(list(correlations) == sorted(correlations), "model_request corr_id values are not increasing")

    sequences: dict[int, list[int]] = defaultdict(list)
    for record in task_events:
        corr = int(record["corr_id"])
        _require(corr in correlations, "task event is not aligned to a model correlation")
        _require(
            correlations[corr] == (int(record["turn_id"]), int(record["request_id"])),
            "task event turn/request does not match its correlation",
        )
        context = record.get("context_seq")
        if _positive_int(context):
            sequences[int(record["agent_pid"])].append(int(context))
    _require(sequences, "task events expose no Context sequence")
    for pid, values in sequences.items():
        _require(values == sorted(values), f"task Context sequence regressed for PID {pid}")


def _validate_model_exchange(
    records: Sequence[dict[str, object]],
    fixture: Sequence[dict[str, object]],
) -> dict[int, dict[str, object]]:
    requests = _select(records, "type", "model_request")
    responses = _select(records, "type", "model_response")
    _require(len(requests) == len(fixture), "model_request count does not match fixture")
    _require(len(responses) == len(fixture), "model_response count does not match fixture")
    positions = {id(record): index for index, record in enumerate(records)}
    response_by_corr: dict[int, dict[str, object]] = {}
    request_positions: list[int] = []
    response_positions: list[int] = []
    for index, (request, response, fixture_record) in enumerate(
        zip(requests, responses, fixture), 1
    ):
        expected_response = fixture_record.get("response")
        _require(isinstance(expected_response, dict), f"fixture response {index} is malformed")
        envelope = (
            request.get("turn_id"),
            request.get("request_id"),
            request.get("corr_id"),
        )
        _require(
            all(_positive_int(value) for value in envelope),
            f"model_request {index} has an invalid envelope",
        )
        _require(
            (
                response.get("turn_id"),
                response.get("request_id"),
                response.get("corr_id"),
            )
            == envelope,
            f"model_response {index} does not match its request envelope",
        )
        request_position = positions[id(request)]
        response_position = positions[id(response)]
        _require(
            request_position < response_position,
            f"model_response {index} precedes its request",
        )
        request_positions.append(request_position)
        response_positions.append(response_position)
        expected_type = expected_response.get("type")
        if expected_type == "tool_use":
            expected = {
                "type": "model_response",
                "turn_id": envelope[0],
                "request_id": envelope[1],
                "corr_id": envelope[2],
                "response_type": "tool_use",
                "tool": expected_response.get("tool"),
                "arguments": expected_response.get("arguments"),
            }
        elif expected_type == "final":
            expected = {
                "type": "model_response",
                "turn_id": envelope[0],
                "request_id": envelope[1],
                "corr_id": envelope[2],
                "response_type": "final",
                "content": expected_response.get("content"),
            }
        else:
            raise ValidationError(f"fixture response {index} has an unsupported type")
        _require(response == expected, f"model_response {index} differs from fixture")
        corr = int(envelope[2])
        _require(corr not in response_by_corr, "model_response corr_id is duplicated")
        response_by_corr[corr] = response
    _require(request_positions == sorted(request_positions), "model_request order changed")
    _require(response_positions == sorted(response_positions), "model_response order changed")

    request_ids_by_turn: dict[int, set[int]] = defaultdict(set)
    for request in requests:
        request_ids_by_turn[int(request["turn_id"])].add(int(request["request_id"]))
    _require(
        set(request_ids_by_turn) == {1, 2, 3}
        and all(len(values) == 1 for values in request_ids_by_turn.values()),
        "model request_id is not stable within each turn",
    )
    turn_request_ids = [next(iter(request_ids_by_turn[turn])) for turn in (1, 2, 3)]
    _require(
        turn_request_ids == sorted(turn_request_ids)
        and len(set(turn_request_ids)) == len(turn_request_ids),
        "model request_id is reused or regressed across turns",
    )

    effect_types = {
        "tool_event",
        "task_event",
        "approval_request",
        "approval_decision",
    }
    for index, (request, response) in enumerate(zip(requests, responses), 1):
        corr = int(request["corr_id"])
        request_position = positions[id(request)]
        response_position = positions[id(response)]
        next_request_position = (
            positions[id(requests[index])] if index < len(requests) else len(records)
        )
        _require(
            response_position < next_request_position,
            f"model round {index} overlaps the next request",
        )
        round_effects = [
            positions[id(record)]
            for record in records
            if record.get("type") in effect_types and record.get("corr_id") == corr
        ]
        _require(
            all(response_position < position < next_request_position for position in round_effects),
            f"model round {index} effects are outside its response boundary",
        )
        _require(
            request_position < response_position,
            f"model round {index} response is not causally ordered",
        )
    return response_by_corr


def _validate_tool_response_chains(
    records: Sequence[dict[str, object]],
    response_by_corr: Mapping[int, dict[str, object]],
) -> None:
    tool_events = _select(records, "type", "tool_event")
    positions = {id(record): index for index, record in enumerate(records)}
    events_by_corr: dict[int, list[dict[str, object]]] = defaultdict(list)
    for event in tool_events:
        corr = event.get("corr_id")
        _require(_positive_int(corr), "tool_event has invalid corr_id")
        response = response_by_corr.get(int(corr))
        _require(
            response is not None and response.get("response_type") == "tool_use",
            "tool_event is attached to a final or unknown model response",
        )
        _require(event.get("tool") == response.get("tool"), "tool_event changed model-selected tool")
        _require(
            (event.get("turn_id"), event.get("request_id"))
            == (response.get("turn_id"), response.get("request_id")),
            "tool_event changed the model response envelope",
        )
        precursors = [
            record
            for record in records
            if record.get("corr_id") == corr
            and record.get("type")
            in ("task_event", "approval_request", "approval_decision")
        ]
        _require(
            all(positions[id(record)] < positions[id(event)] for record in precursors),
            f"tool result corr {corr} precedes its TASK or approval effects",
        )
        events_by_corr[int(corr)].append(event)

    for corr, response in response_by_corr.items():
        if response.get("response_type") == "tool_use":
            _require(
                len(events_by_corr.get(corr, [])) == 1,
                f"tool_use corr {corr} lacks exactly one tool_event",
            )
        else:
            _require(not events_by_corr.get(corr), f"final corr {corr} produced a tool_event")

    task_events = _select(records, "type", "task_event")
    for corr, response in response_by_corr.items():
        if response.get("response_type") != "tool_use":
            continue
        arguments = response.get("arguments")
        _require(isinstance(arguments, dict), f"tool_use corr {corr} has malformed arguments")
        tool = response.get("tool")
        event = events_by_corr[corr][0]
        if tool == "read_artifact":
            _require(
                _status_is(event, 0) and event.get("value0") == arguments.get("handle"),
                f"read_artifact corr {corr} is not bound to its requested handle",
            )
        if tool != "delegate_task":
            continue
        children = [
            item
            for item in task_events
            if item.get("corr_id") == corr and int(item.get("parent_task_id", 0)) != 0
        ]
        assigned = [item for item in children if item.get("event") == "assigned"]
        _require(len(assigned) == 1, f"delegate_task corr {corr} lacks one child assignment")
        task_id = int(assigned[0]["task_id"])
        _require(
            all(int(item["task_id"]) == task_id for item in children),
            f"delegate_task corr {corr} emitted more than one child task",
        )
        expected_role = arguments.get("role")
        _require(
            all(item.get("role") == expected_role for item in children),
            f"delegate_task corr {corr} changed its selected role",
        )
        _require(
            assigned[0].get("summary") == arguments.get("objective"),
            f"delegate_task corr {corr} changed its canonical objective",
        )
        terminals = [
            item
            for item in children
            if item.get("event") in ("completed", "failed", "cancelled")
        ]
        _require(len(terminals) == 1, f"delegate_task corr {corr} lacks one terminal")
        terminal = terminals[0]
        _require(
            event.get("status") == terminal.get("status"),
            f"delegate_task corr {corr} tool status contradicts its terminal",
        )
        artifacts = [item for item in children if item.get("event") == "artifact_published"]
        if terminal.get("event") == "completed":
            _require(len(artifacts) == 1, f"delegate_task corr {corr} completed without one artifact")
            _require(
                event.get("value0") == artifacts[0].get("artifact_handle")
                and event.get("value1") == task_id
                and event.get("value2") == assigned[0].get("agent_id"),
                f"delegate_task corr {corr} tool result is not bound to its artifact/task/Agent",
            )
            expected_result = {
                "system": "system_artifact_ready",
                "research": "research_artifact_ready",
                "analyst": "analyst_report_ready",
            }.get(str(expected_role))
            _require(
                event.get("result") == expected_result,
                f"delegate_task corr {corr} tool result contradicts its completed role",
            )
        elif terminal.get("event") == "failed":
            _require(not artifacts, f"delegate_task corr {corr} failed but published an artifact")
            _require(
                all(event.get(key) == 0 for key in ("value0", "value1", "value2")),
                f"delegate_task corr {corr} failure reports a nonzero effect",
            )
            result = event.get("result")
            tokens = _field_tokens(str(result)) if isinstance(result, str) else set()
            _require(
                "task_failed" in tokens and "replan_allowed=1" in tokens,
                f"delegate_task corr {corr} failure result is not replannable",
            )


def _validate_controller_order(records: Sequence[dict[str, object]]) -> None:
    positions = {id(record): index for index, record in enumerate(records)}
    completions = _select(records, "type", "turn_complete")
    completion_by_turn = {int(record["turn_id"]): positions[id(record)] for record in completions}
    for turn in (1, 2, 3):
        turn_records = [
            record
            for record in records
            if record.get("type") in ("model_request", "model_response", "tool_event", "task_event")
            and record.get("turn_id") == turn
        ]
        _require(turn_records, f"turn {turn} has no business events")
        _require(
            all(positions[id(record)] < completion_by_turn[turn] for record in turn_records),
            f"turn {turn} completed before its business events",
        )
        if turn < 3:
            next_requests = [
                positions[id(record)]
                for record in _select(records, "type", "model_request")
                if record.get("turn_id") == turn + 1
            ]
            _require(
                next_requests and completion_by_turn[turn] < min(next_requests),
                f"turn {turn + 1} started before turn {turn} completed",
            )

    approvals = _select(records, "type", "approval_request")
    decisions = _select(records, "type", "approval_decision")
    publishes = [
        record
        for record in _select(records, "type", "tool_event")
        if record.get("tool") == "publish_report"
    ]
    _require(len(approvals) == len(decisions) == len(publishes) == 1, "approval ordering is ambiguous")
    _require(
        positions[id(approvals[0])]
        < positions[id(decisions[0])]
        < positions[id(publishes[0])]
        < completion_by_turn[3],
        "approval request/decision/tool result/turn completion order is invalid",
    )
    closed = _select(records, "type", "session_closed")
    _require(len(closed) == 1, "controller lacks a unique session_closed event")
    _require(positions[id(closed[0])] == len(records) - 1, "controller emitted records after session_closed")


def _validate_controller(
    records: Sequence[dict[str, object]],
    fixture_digests: Sequence[str],
    fixture: Sequence[dict[str, object]],
) -> tuple[
    str,
    set[tuple[str, int, int, int]],
    dict[int, tuple[str, int, int, int]],
]:
    error_types = ("error", "model_error", "daemon_error")
    errors = [record for record in records if record.get("type") in error_types]
    _require(not errors, "controller transcript contains an error event")

    ready = _select(records, "type", "session_ready")
    _require(len(ready) == 1, "controller lacks a unique session_ready event")
    _require(ready[0].get("guest_profile") == "nexus", "controller attached to a non-Nexus Guest")
    session_id = ready[0].get("session_id")
    _require(isinstance(session_id, str) and bool(session_id), "session_ready has no session_id")
    ready_position = next(index for index, record in enumerate(records) if record is ready[0])
    _require(
        all(record.get("type") == "welcome" for record in records[:ready_position]),
        "controller emitted session data before session_ready",
    )

    requests = _select(records, "type", "model_request")
    _require(len(requests) == len(fixture_digests), "model_request count does not match fixture")
    _require(
        [record.get("request_sha256") for record in requests] == list(fixture_digests),
        "model_request digests do not exactly match fixture order",
    )
    _require(
        {record.get("turn_id") for record in requests} == {1, 2, 3},
        "model requests do not cover exactly three user turns",
    )
    response_by_corr = _validate_model_exchange(records, fixture)

    completions = _select(records, "type", "turn_complete")
    _require(len(completions) == 3, "expected exactly three turn_complete events")
    _require(
        [record.get("turn_id") for record in completions] == [1, 2, 3],
        "turn_complete events are not ordered turns 1, 2, 3",
    )
    _require(
        all(record.get("status") == "completed" for record in completions),
        "a Nexus replay turn did not complete",
    )
    _require(
        all(isinstance(record.get("answer"), str) and bool(str(record["answer"]).strip()) for record in completions),
        "a Nexus replay turn has no final answer",
    )
    request_id_by_turn = {
        int(request["turn_id"]): int(request["request_id"])
        for request in requests
    }
    _require(
        all(
            _positive_int(record.get("request_id"))
            and record.get("request_id") == request_id_by_turn.get(int(record["turn_id"]))
            for record in completions
        ),
        "turn_complete is not bound to its active request_id",
    )
    fixture_finals = [
        record["response"].get("content")
        for record in fixture
        if isinstance(record.get("response"), dict)
        and record["response"].get("type") == "final"
    ]
    _require(
        fixture_finals == [record.get("answer") for record in completions],
        "controller final answers do not exactly match the digest-bound fixture",
    )
    final_answer = str(completions[-1]["answer"]).lower()
    _require(
        any(token in final_answer for token in ("denied", "unpublished", "not published")),
        "final turn does not safely report the publication denial",
    )
    _validate_controls(records)

    task_events = _select(records, "type", "task_event")
    _require(task_events, "controller transcript contains no task_event")
    for index, record in enumerate(task_events, 1):
        _validate_task_shape(record, f"task_event {index}")
    kinds = {record.get("event") for record in task_events}
    for required in ("assigned", "accepted", "progress", "failed", "completed", "artifact_published"):
        _require(required in kinds, f"Nexus transcript lacks TASK_{required.upper()}")
    _validate_identities(task_events)
    _validate_task_dag(task_events)
    tool_events = _select(records, "type", "tool_event")
    _require(tool_events, "controller transcript contains no tool_event")
    for index, record in enumerate(tool_events, 1):
        _validate_tool_shape(record, f"tool_event {index}")
    _validate_tool_response_chains(records, response_by_corr)
    (
        system_handle,
        research_handle,
        analyst_handle,
        system_digest,
        research_digest,
        _,
        system_facts,
        scheduler_facts,
    ) = _validate_artifacts(task_events)
    _validate_fixture_artifact_flow(
        fixture, system_handle, research_handle, analyst_handle
    )
    final_tokens = _field_tokens(final_answer)
    _require(SOURCE_REVISION_TOKEN in final_tokens, "final answer omits source revision")
    for token in MEASUREMENT_TOKENS:
        _require(token in final_tokens, f"final answer omits {token}")
    for token in system_facts:
        _require(token in final_tokens, f"final answer omits dynamic System fact {token}")
    _require(
        any(token in final_tokens for token in scheduler_facts),
        "final answer omits a verified this-boot scheduler fact",
    )
    for token in (
        f"system_handle={system_handle}",
        f"research_handle={research_handle}",
    ):
        _require(token in final_tokens, f"final answer omits verified source {token}")
    _validate_approval_and_tools(
        records, research_handle, analyst_handle, response_by_corr
    )
    _validate_context_alignment(task_events, requests)
    _validate_controller_order(records)

    closed = _select(records, "type", "session_closed")
    _require(len(closed) == 1, "controller lacks a unique session_closed event")
    identities = {
        (
            str(record["role"]),
            int(record["agent_pid"]),
            int(record["agent_id"]),
            int(record["control_id"]),
        )
        for record in task_events
        if record.get("role") in BUSINESS_ROLES
        and record.get("control_id_known") is True
    }
    worker_tasks = WorkerTaskMap(task_events, tool_events)
    for record in task_events:
        if (
            record.get("event") != "assigned"
            or record.get("role") not in ("system", "research", "analyst")
            or record.get("control_id_known") is not True
        ):
            continue
        task_id = int(record["task_id"])
        identity = (
            str(record["role"]),
            int(record["agent_pid"]),
            int(record["agent_id"]),
            int(record["control_id"]),
        )
        previous = worker_tasks.setdefault(task_id, identity)
        _require(previous == identity, f"task {task_id} changed worker identity")
    _require(worker_tasks, "controller exposed no delegated worker task identities")
    return str(session_id), identities, worker_tasks


def _validate_observer(
    records: Sequence[dict[str, object]],
    session_id: str,
    controller_identities: set[tuple[str, int, int, int]],
    worker_tasks: Mapping[int, tuple[str, int, int, int]],
) -> None:
    _require(
        all(record.get("type") == "telemetry" for record in records),
        "observer transcript contains a non-telemetry record",
    )
    for index, record in enumerate(records, 1):
        leaked = OBSERVER_FORBIDDEN_FIELDS.intersection(record)
        _require(
            not leaked,
            f"observer record {index} leaks controller-only fields: {','.join(sorted(leaked))}",
        )
    attached = _select(records, "event", "observer_attached")
    _require(len(attached) == 1, "observer did not record its attach handshake")
    _require(attached[0].get("session_id") == session_id, "observer attached to another session")
    _require(attached[0].get("guest_profile") == "nexus", "observer attached to a non-Nexus Guest")
    _require(_select(records, "event", "waiting_llm"), "observer saw no waiting_llm snapshot")

    audits = [
        record
        for record in records
        if record.get("event") == "kernel_audit"
        or record.get("source") == "kernel_audit"
    ]
    _require(audits, "observer saw no Guest-origin fresh kernel audit record")
    audit_sequences: list[int] = []
    audit_identities: dict[int, tuple[str, int, int, int]] = {}
    lifecycle: tuple[int, int] | None = None
    for index, record in enumerate(audits, 1):
        label = f"kernel_audit {index}"
        _require("context_seq" not in record, f"{label} confuses audit sequence with Context sequence")
        _require(set(record) == set(KERNEL_AUDIT_FIELDS), f"{label} fields do not match Host schema")
        _require(record.get("type") == "telemetry", f"{label} has an invalid record type")
        _require(record.get("fresh") is True, f"{label} is not fresh")
        _require(_wire_u64(record.get("record_sequence"), positive=True), f"{label} has no audit sequence")
        _require(_wire_u64(record.get("tick")), f"{label} has invalid tick")
        for key in (
            "workflow_lifecycle_id",
            "workflow_lifecycle_generation",
            "actor_control_id",
        ):
            _require(_wire_u64(record.get(key), positive=True), f"{label} has invalid {key}")
        for key in ("pid", "agent_id", "source_pid", "target_pid"):
            _require(_i32(record.get(key), positive=True), f"{label} has invalid {key}")
        for key in ("audit_kind", "loop_state", "tool_id", "event_type"):
            _require(_u32(record.get(key)), f"{label} has invalid {key}")
        for key in ("value0", "value1", "value2", "provenance"):
            _require(_wire_u64(record.get(key)), f"{label} has invalid {key}")
        _require(_i32(record.get("status")), f"{label} has invalid status")
        _require(record.get("role") in BUSINESS_ROLES, f"{label} has invalid role")
        sequence = int(record["record_sequence"])
        audit_sequences.append(sequence)
        current_lifecycle = (
            int(record["workflow_lifecycle_id"]),
            int(record["workflow_lifecycle_generation"]),
        )
        if lifecycle is None:
            lifecycle = current_lifecycle
        _require(current_lifecycle == lifecycle, f"{label} changed workflow lifecycle")
        identity = (
            str(record["role"]),
            int(record["pid"]),
            int(record["agent_id"]),
            int(record["actor_control_id"]),
        )
        previous = audit_identities.setdefault(int(record["pid"]), identity)
        _require(previous == identity, f"{label} changed the kernel identity for one PID")
    _require(audit_sequences == sorted(audit_sequences), "kernel audit sequences regressed")
    _require(len(audit_sequences) == len(set(audit_sequences)), "kernel audit sequences are duplicated")

    _require(
        set(audit_identities.values()).issubset(controller_identities),
        "kernel audit and controller disagree on an Agent identity",
    )
    coordinator_identities = [
        identity for identity in controller_identities if identity[0] == "coordinator"
    ]
    _require(
        len(coordinator_identities) == 1,
        "controller does not expose one unambiguous Coordinator identity",
    )
    coordinator_identity = coordinator_identities[0]
    for task_id, expected_identity in worker_tasks.items():
        _require(
            audit_identities.get(expected_identity[1]) == expected_identity,
            f"task {task_id} worker identity is not backed by kernel audit",
        )

    message_audits: dict[int, list[dict[str, object]]] = defaultdict(list)
    for record in audits:
        kind = int(record["audit_kind"])
        _require(kind in (2, 3), "kernel audit uses a non-MESSAGE kind rejected by the Host")
        _require(record.get("event_type") == 2, "TASK transport is not a kernel MESSAGE event")
        _require(_positive_int(record.get("value0")), "MESSAGE audit has no kernel event id")
        _require(_positive_int(record.get("value1")), "MESSAGE audit has no task correlation")
        _require(
            record.get("provenance") == 0,
            "MESSAGE audit must preserve the kernel's zero/unavailable provenance",
        )
        _require(record.get("value2") == record.get("target_pid"), "MESSAGE audit target projection changed")
        message_audits[int(record["value1"])].append(record)

    saw_waiting_enqueue = False
    for task_id, expected_identity in worker_tasks.items():
        task_audits = message_audits.get(task_id, [])
        by_event_id: dict[int, list[dict[str, object]]] = defaultdict(list)
        for record in task_audits:
            by_event_id[int(record["value0"])].append(record)
        pairs: list[tuple[dict[str, object], dict[str, object]]] = []
        for event_id, event_records in by_event_id.items():
            enqueues = [record for record in event_records if record.get("audit_kind") == 2]
            consumes = [record for record in event_records if record.get("audit_kind") == 3]
            _require(
                len(enqueues) == 1 and len(consumes) == 1,
                f"task {task_id} kernel event {event_id} lacks one enqueue/consume pair",
            )
            enqueue, consume = enqueues[0], consumes[0]
            _require(
                int(enqueue["record_sequence"]) < int(consume["record_sequence"]),
                f"task {task_id} kernel event {event_id} was consumed before enqueue",
            )
            _require(
                enqueue.get("source_pid") == consume.get("source_pid")
                and enqueue.get("target_pid") == consume.get("target_pid"),
                f"task {task_id} kernel event {event_id} changed route",
            )
            _require(
                enqueue.get("value1") == task_id and consume.get("value1") == task_id,
                f"task {task_id} lost its kernel correlation",
            )
            _require(
                consume.get("loop_state") == 2,
                f"task {task_id} kernel event {event_id} consume did not observe RUNNING",
            )
            pairs.append((enqueue, consume))

        worker_pid = expected_identity[1]
        coordinator_pid = coordinator_identity[1]
        assignments = [
            pair
            for pair in pairs
            if pair[0].get("source_pid") == coordinator_pid
            and pair[0].get("target_pid") == worker_pid
        ]
        _require(assignments, f"task {task_id} has no Coordinator-to-worker MESSAGE pair")
        for pair in assignments:
            for record in pair:
                _require(
                    (
                        record.get("role"),
                        record.get("pid"),
                        record.get("agent_id"),
                        record.get("actor_control_id"),
                    )
                    == expected_identity,
                    f"task {task_id} assignment audit identity does not match TASK_EVENT",
                )
        saw_waiting_enqueue = saw_waiting_enqueue or any(
            enqueue.get("loop_state") == 3 for enqueue, _ in assignments
        )

        responses = [
            pair
            for pair in pairs
            if pair[0].get("source_pid") == worker_pid
            and pair[0].get("target_pid") == coordinator_pid
        ]
        _require(responses, f"task {task_id} has no worker-to-Coordinator MESSAGE pair")
        for pair in responses:
            for record in pair:
                _require(
                    (
                        record.get("role"),
                        record.get("pid"),
                        record.get("agent_id"),
                        record.get("actor_control_id"),
                    )
                    == coordinator_identity,
                    f"task {task_id} response audit identity does not match Coordinator",
                )
    _require(saw_waiting_enqueue, "kernel audit saw no enqueue wake a WAITING worker")

    snapshots = [
        record
        for record in records
        if record.get("event") == "kernel_snapshot"
        or record.get("source") == "kernel_snapshot"
    ]
    _require(snapshots, "observer saw no worker kernel snapshot")
    snapshot_roles: set[str] = set()
    for index, record in enumerate(snapshots, 1):
        label = f"kernel_snapshot {index}"
        _require(set(record) == set(KERNEL_SNAPSHOT_FIELDS), f"{label} fields do not match Host schema")
        _require(record.get("type") == "telemetry", f"{label} has an invalid record type")
        _require(record.get("fresh") is False, f"{label} is marked fresh")
        _require("record_sequence" not in record, f"{label} exposes an audit record sequence")
        for key in (
            "tick",
            "actor_control_id",
            "workflow_lifecycle_id",
            "workflow_lifecycle_generation",
            "capability_mask",
            "context_seq",
            "wait_sleep_delta",
            "wait_wakeup_delta",
            "sched_dispatch",
            "sched_dispatch_count",
            "sched_budget",
            "sched_budget_used",
            "sched_vruntime",
        ):
            _require(_wire_u64(record.get(key)), f"{label} has invalid {key}")
        for key in ("pid", "agent_id"):
            _require(_i32(record.get(key), positive=True), f"{label} has invalid {key}")
        _require(_u32(record.get("loop_state")), f"{label} has invalid loop_state")
        _require(_wire_u64(record.get("actor_control_id"), positive=True), f"{label} has invalid actor_control_id")
        _require(_positive_int(record.get("capability_mask")), f"{label} has no capability evidence")
        _require(_positive_int(record.get("workflow_lifecycle_id")), f"{label} has invalid lifecycle id")
        _require(_positive_int(record.get("workflow_lifecycle_generation")), f"{label} has invalid lifecycle generation")
        _require(_positive_int(record.get("context_seq")), f"{label} has no Context progress")
        _require(_positive_int(record.get("wait_sleep_delta")), f"{label} saw no nonbusy wait")
        _require(_positive_int(record.get("wait_wakeup_delta")), f"{label} saw no worker wakeup")
        _require(_positive_int(record.get("sched_dispatch")), f"{label} saw no scheduler dispatch")
        _require(_positive_int(record.get("sched_budget")), f"{label} has no scheduler budget")
        _require(
            _positive_int(record.get("sched_budget_used")),
            f"{label} has no positive scheduler budget accounting",
        )
        _require(
            int(record["sched_dispatch_count"]) >= int(record["sched_dispatch"]),
            f"{label} absolute dispatch count is smaller than its delta",
        )
        _require(
            (int(record["workflow_lifecycle_id"]), int(record["workflow_lifecycle_generation"]))
            == lifecycle,
            f"{label} changed workflow lifecycle",
        )
        role = record.get("role")
        _require(role in BUSINESS_ROLES, f"{label} has invalid role")
        snapshot_roles.add(str(role))
        audit_identity = audit_identities.get(int(record["pid"]))
        _require(audit_identity is not None, f"{label} identity lacks kernel audit backing")
        _require(
            audit_identity
            == (
                str(role),
                int(record["pid"]),
                int(record["agent_id"]),
                int(record["actor_control_id"]),
            ),
            f"{label} identity disagrees with kernel audit",
        )
    _require(snapshot_roles == BUSINESS_ROLES, "observer lacks snapshots for all four business roles")

    tool_projection_fields = (
        "turn_id",
        "request_id",
        "corr_id",
        "tool",
        "status",
        "sequence",
        "value0",
        "value1",
        "value2",
        "context_seq",
        "provenance",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
    )
    controller_tools = getattr(worker_tasks, "controller_tools", None)
    _require(
        isinstance(controller_tools, tuple),
        "validator lost the controller tool projection",
    )
    observed_tools = [
        record
        for record in records
        if record.get("event") == "tool_event"
        or (record.get("source") == "guest" and record.get("tool") in NEXUS_PRODUCT_TOOLS)
    ]
    for index, record in enumerate(observed_tools, 1):
        _validate_tool_shape(record, f"observer tool_event {index}", observer=True)
    expected_tool_projection = [
        tuple(record.get(field) for field in tool_projection_fields)
        for record in controller_tools
    ]
    observed_tool_projection = [
        tuple(record.get(field) for field in tool_projection_fields)
        for record in observed_tools
    ]
    _require(
        observed_tool_projection == expected_tool_projection,
        "observer tool metadata does not exactly match the controller projection",
    )

    observed_tasks = [record for record in records if record.get("event") in TASK_EVENTS]
    for index, record in enumerate(observed_tasks, 1):
        _validate_task_shape(record, f"observer task_event {index}", observer=True)
    projection_fields = (
        "event",
        "task_state",
        "turn_id",
        "request_id",
        "corr_id",
        "task_id",
        "parent_task_id",
        "workflow_lifecycle_id",
        "workflow_lifecycle_generation",
        "role",
        "agent_pid",
        "agent_id",
        "control_id_known",
        "control_id",
        "status",
        "tick",
        "deadline_tick",
        "context_seq",
        "provenance",
        "metric_code",
        "metric_value",
        "artifact_handle",
        "resource_used",
        "source_pid",
        "target_pid",
    )
    controller_tasks = getattr(worker_tasks, "controller_tasks", None)
    _require(
        isinstance(controller_tasks, tuple),
        "validator lost the controller TASK projection",
    )
    expected_projection = [
        tuple(record.get(field) for field in projection_fields)
        for record in controller_tasks
    ]
    observed_projection = [
        tuple(record.get(field) for field in projection_fields)
        for record in observed_tasks
    ]
    _require(
        observed_projection == expected_projection,
        "observer TASK metadata does not exactly match the controller projection",
    )
    observed_kinds = {record.get("event") for record in observed_tasks}
    for required in ("assigned", "accepted", "progress", "failed", "completed", "artifact_published"):
        _require(required in observed_kinds, f"observer missed task event {required}")
    waiting = [
        (index, record)
        for index, record in enumerate(observed_tasks)
        if record.get("task_state") == "waiting"
    ]
    _require(waiting, "observer saw no nonbusy worker waiting state")
    _require(
        all(
            any(
                later.get("task_id") == waiting_record.get("task_id")
                and later.get("event")
                in ("progress", "completed", "failed", "cancelled")
                and later.get("task_state") != "waiting"
                for later in observed_tasks[waiting_index + 1 :]
            )
            for waiting_index, waiting_record in waiting
        ),
        "observer resume must occur strictly after its waiting state",
    )

    observer_identities = {
        (
            str(record.get("role")),
            int(record["agent_pid"]),
            int(record["agent_id"]),
            int(record["control_id"]),
        )
        for record in observed_tasks
        if record.get("role") in BUSINESS_ROLES
        and record.get("control_id_known") is True
        and _positive_int(record.get("agent_pid"))
        and _positive_int(record.get("agent_id"))
        and _positive_int(record.get("control_id"))
    }
    if observer_identities:
        _require(
            observer_identities.issubset(controller_identities),
            "Guest task telemetry and controller disagree on an Agent identity",
        )

    completed_turns = {
        int(record["turn_id"])
        for record in _select(records, "event", "turn_complete")
        if _positive_int(record.get("turn_id"))
    }
    _require(completed_turns.issuperset({1, 2, 3}), "observer missed a completed user turn")
    closed = _select(records, "event", "session_closed")
    _require(len(closed) == 1, "observer lacks a unique session_closed snapshot")
    closed_position = next(index for index, record in enumerate(records) if record is closed[0])
    _require(
        closed_position == len(records) - 1,
        "observer emitted telemetry after session_closed",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate controller and observer NDJSON from agentos-nexus-replay."
    )
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        fixture = _load_jsonl(args.fixture, "replay fixture")
        controller = _load_jsonl(args.controller, "controller transcript")
        observer = _load_jsonl(args.observer, "observer transcript")
        digests = _fixture_digests(fixture)
        session_id, identities, worker_tasks = _validate_controller(
            controller, digests, fixture
        )
        _validate_observer(observer, session_id, identities, worker_tasks)
    except ValidationError as error:
        print(f"agentos-nexus-replay: FAIL: {error}", file=sys.stderr)
        return 1

    print(
        "agentos-nexus-replay: PASS "
        f"({len(digests)} digests, 3 turns, 4 roles, replan, denied publish, clean close)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
