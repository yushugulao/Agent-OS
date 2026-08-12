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
AGENT_STATUS_BAD_PARAM = -4
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
CANONICAL_DELEGATE_OBJECTIVES = {
    "system": "kernel snapshot this_boot",
    "research": "verify paired evidence",
    "analyst": "synth report",
}
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
PERF_SOURCE_REVISION = "2b14fb1f74b9bd093e6de939a16554620835699e"
SOURCE_TABLE = "one_shot_metrics/data/20260811/tables/contest_paired.csv"
SOURCE_TABLE_SHA256 = "3fafa718df3f9d2cf84311163ef71d7176d30271aa1b77d0eff12e065595065e"
CORE_SOURCE = "os/agent_metadata_query.c:agent_metadata_query_execute_snapshot"
CORE_SHA256 = "1a95220a0ce3f900f7caaf7ae6f2d3dd58b0d1d6d5461f5253de67b15baab64b"
OUTER_SOURCE = "user/src/labdemo_ucore.c:seed_native_workload"
OUTER_SHA256 = "9e8ccb1d27750a41535324063cca9a93f0f624e569aebb6c4294f5a5b4ff8964"

# This is the complete v4 Research payload, in the order materialized by the
# Guest.  TASK_EVENT carries a bounded projection; its artifact digest binds
# the projection to all of the evidence that could not fit on that wire event.
RESEARCH_EVIDENCE_FIELDS = (
    ("schema", "agentos.nexus.live_query_evidence.v1"),
    ("perf_source_revision", PERF_SOURCE_REVISION),
    ("source_table", SOURCE_TABLE),
    ("benchmark", "live_query_paired"),
    ("scope", "historical_not_this_boot"),
    ("samples", "16"),
    ("order_balance", "8/8"),
    ("core_us", "34712.5/13293.5"),
    ("core_paired_ratio_median", "3.118"),
    ("core_indexed_wins", "16/16"),
    ("e2e_us", "711283.5/723928"),
    ("e2e_paired_delta_us", "13452"),
    ("e2e_indexed_wins", "3/16"),
    ("outer_us", "675901/706477"),
    ("outer_definition", "e2e_minus_core"),
    ("outer_paired_delta_us", "33477"),
    ("outer_indexed_wins", "0/16"),
    ("records_examined", "97/2"),
    ("workload_syscalls", "298/10"),
    ("core_source", CORE_SOURCE),
    ("core_sha256", CORE_SHA256),
    ("core_mechanism", "indexed_candidate_scan"),
    ("core_constraint", "scope_visibility_and_snapshot_stability"),
    ("outer_source", OUTER_SOURCE),
    ("outer_sha256", OUTER_SHA256),
    ("outer_mechanism", "corpus_seed_io"),
    ("claim", "historical_snapshot"),
)
RESEARCH_EVIDENCE = dict(RESEARCH_EVIDENCE_FIELDS)
RESEARCH_EVIDENCE_PAYLOAD = "".join(
    f"{key}={value}\n" for key, value in RESEARCH_EVIDENCE_FIELDS
)
RESEARCH_ARTIFACT_SHA256 = hashlib.sha256(
    RESEARCH_EVIDENCE_PAYLOAD.encode("utf-8")
).hexdigest()
RESEARCH_EVENT_KEYS = (
    "benchmark",
    "scope",
    "core_us",
    "core_indexed_wins",
    "e2e_us",
    "e2e_indexed_wins",
    "outer_us",
    "outer_indexed_wins",
)
RESEARCH_EVENT_SUMMARY = ";".join(
    f"{key}={RESEARCH_EVIDENCE[key]}" for key in RESEARCH_EVENT_KEYS
)

ANALYST_FINDING = "core_wins_16/16;e2e_wins_3/16;outer_path_erases_gain"
ANALYST_ACTIONS = (
    "keep_index;add_phase_timing_outside_core_window",
    "optimize_measured_outer_phase_after_timing",
)
ANALYST_VALIDATION = "e2e_median_delta_lte_0;core_wins_16/16;equal_hash_scope"
ANALYST_ROLLBACK = "e2e_p95_gt_5pct_or_hash_scope_mismatch"
OBSERVER_FORBIDDEN_FIELDS = frozenset(
    ("arguments", "canonical_arguments", "content", "objective", "raw", "result", "summary")
)


class ValidationError(ValueError):
    """A Nexus replay artifact does not satisfy the acceptance contract."""


TaskKey = tuple[int, int, int]


def _task_key(record: Mapping[str, object]) -> TaskKey:
    return (
        int(record["turn_id"]),
        int(record["request_id"]),
        int(record["task_id"]),
    )


def _task_label(task_key: TaskKey) -> str:
    turn_id, request_id, task_id = task_key
    return f"task {task_id} in turn {turn_id}/request {request_id}"


class WorkerTaskMap(dict[TaskKey, tuple[str, int, int, int]]):
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


def _generation_safe_handle(value: object) -> bool:
    return (
        _positive_int(value)
        and int(value) <= MAX_U32
        and ((int(value) >> 16) & 0xFFFF) > 0
        and 0 < (int(value) & 0xFFFF) <= 32
    )


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


def _research_analysis_summary() -> str:
    evidence = RESEARCH_EVIDENCE
    return (
        f"scope={evidence['scope']};benchmark={evidence['benchmark']};"
        f"perf_revision={evidence['perf_source_revision']};"
        f"core={evidence['core_us']},ratio={evidence['core_paired_ratio_median']},"
        f"wins={evidence['core_indexed_wins']};"
        f"e2e={evidence['e2e_us']},delta={evidence['e2e_paired_delta_us']},"
        f"wins={evidence['e2e_indexed_wins']};"
        f"outer={evidence['outer_us']},delta={evidence['outer_paired_delta_us']},"
        f"wins={evidence['outer_indexed_wins']};"
        f"source={evidence['core_source']};source_sha={evidence['core_sha256']};"
        f"mechanism={evidence['core_mechanism']};"
        f"constraint={evidence['core_constraint']}"
    )


def _analyst_report_payload(
    system_handle: int,
    research_handle: int,
    sched_budget: int,
    requested_focus: str,
) -> str:
    return (
        "schema=agentos.nexus.report.v2\n"
        f"system_handle={system_handle}\n"
        f"research_handle={research_handle}\n"
        f"requested_focus={requested_focus}\n"
        f"system_evidence=scope=this_boot;sched_budget={sched_budget}\n"
        f"research_evidence={_research_analysis_summary()}\n"
        f"core_ratio={RESEARCH_EVIDENCE['core_paired_ratio_median']}\n"
        f"e2e_wins={RESEARCH_EVIDENCE['e2e_indexed_wins']}\n"
        f"finding={ANALYST_FINDING}\n"
        f"action_1={ANALYST_ACTIONS[0]}\n"
        f"action_2={ANALYST_ACTIONS[1]}\n"
        f"validation={ANALYST_VALIDATION}\n"
        f"rollback={ANALYST_ROLLBACK}\n"
    )


def _analyst_event_summary(
    system_handle: int, research_handle: int, sched_budget: int
) -> str:
    return (
        f"system_handle={system_handle};research_handle={research_handle};"
        f"core_ratio={RESEARCH_EVIDENCE['core_paired_ratio_median']};"
        f"e2e_wins={RESEARCH_EVIDENCE['e2e_indexed_wins']};"
        f"sched_budget={sched_budget}"
    )


def _validate_final_answer(final_answer: str, scheduler_fact: str) -> None:
    _require(
        len(final_answer.encode("utf-8")) <= 512,
        "final answer exceeds the 512-byte Nexus completion contract",
    )
    match = re.fullmatch(r"sched_budget=([1-9][0-9]*)", scheduler_fact)
    _require(match is not None, "System scheduler fact has no canonical budget")
    canonical = (
        f"AgentOS Live Query;this_boot=live,b={match.group(1)};"
        "historical_not_this_boot;core=3.118x,16/16;"
        "E2E=+13.452ms,3/16;outer=+33.477ms;"
        "action1=phase timing;action2=outer optimization;"
        "validation=E2E<=baseline,core=16/16,equal hash/scope;"
        "rollback=E2E p95>5% or hash/scope mismatch;publication=denied"
    )
    candidate = final_answer.strip(" \t\n\r")
    if candidate.endswith("."):
        candidate = candidate[:-1]
    _require(
        candidate.casefold() == canonical.casefold(),
        "final answer does not exactly match the current-budget, historical-evidence, denied-publication canonical block",
    )


def _validate_source_evidence_files() -> None:
    root = Path(__file__).resolve().parent.parent
    sources = (
        (SOURCE_TABLE, SOURCE_TABLE_SHA256, None),
        (*CORE_SOURCE.split(":", 1), CORE_SHA256),
        (*OUTER_SOURCE.split(":", 1), OUTER_SHA256),
    )
    for entry in sources:
        if len(entry) == 3 and entry[2] is None:
            relative, expected_digest, symbol = entry
        else:
            relative, symbol, expected_digest = entry
        path = root / str(relative)
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ValidationError(f"cannot read frozen evidence source {relative}: {error}") from error
        digest = hashlib.sha256(payload).hexdigest()
        _require(
            digest == expected_digest,
            f"frozen evidence source {relative} has SHA-256 {digest}, expected {expected_digest}",
        )
        if symbol is not None:
            try:
                text = payload.decode("utf-8")
            except UnicodeError as error:
                raise ValidationError(f"source module {relative} is not UTF-8: {error}") from error
            _require(
                re.search(rf"\b{re.escape(str(symbol))}\s*\(", text) is not None,
                f"source module {relative} does not define {symbol}",
            )


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
        return _generation_safe_handle(value)

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
    _require(len(research_delegates) == 2, "turn 2 does not contain exactly one failed Research attempt and one replan")
    research_handles = [arguments(call).get("input_handle") for call in research_delegates]
    _require(not valid_handle(research_handles[0]), "turn 2 first Research source is not deliberately invalid")
    _require(
        valid_handle(research_handles[1]) and research_handles[1] != research_handles[0],
        "turn 2 does not replan with a distinct generation-safe local source handle",
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
    grouped: dict[TaskKey, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    for index, record in enumerate(task_events):
        grouped[_task_key(record)].append((index, record))

    parents: dict[TaskKey, TaskKey | None] = {}
    for task_key, events in grouped.items():
        task_id = task_key[2]
        label = _task_label(task_key)
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
        _require(len(identities) == 1, f"{label} changed Agent identity")
        values = {int(record["parent_task_id"]) for _, record in events}
        _require(len(values) == 1, f"{label} changed parent_task_id")
        parent = values.pop()
        _require(parent != task_id, f"{label} is its own parent")
        parents[task_key] = (
            None if parent == 0 else (task_key[0], task_key[1], parent)
        )
        if parent != 0:
            _require(
                all("deadline_tick" in record for _, record in events),
                f"{label} lacks its delegated-task deadline",
            )
            deadlines = {
                int(record["deadline_tick"])
                for _, record in events
            }
            _require(
                len(deadlines) == 1,
                f"{label} changed its delegated-task deadline",
            )
            deadline = next(iter(deadlines))
            _require(deadline > 0, f"{label} has an invalid deadline")
            _require(
                all(deadline > int(record["tick"]) for _, record in events),
                f"{label} deadline is not in the future",
            )
        turn_requests = {
            (int(record["turn_id"]), int(record["request_id"]))
            for _, record in events
        }
        _require(
            len(turn_requests) == 1,
            f"{label} changed its active turn/request envelope",
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
                f"delegated {label} changed its model response envelope",
            )
        kinds = [str(record["event"]) for _, record in events]
        _require(
            kinds.count("assigned") == 1,
            f"{label} lacks exactly one TASK_ASSIGN",
        )
        assigned = kinds.index("assigned")
        _require(assigned == 0, f"{label} emitted an event before assignment")
        _require(
            kinds.count("accepted") == 1,
            f"{label} lacks exactly one TASK_ACCEPT",
        )
        accepted = kinds.index("accepted")
        _require(assigned < accepted, f"{label} accepted before assignment")
        progress_positions = [
            index for index, kind in enumerate(kinds) if kind == "progress"
        ]
        _require(progress_positions, f"{label} lacks TASK_PROGRESS")
        _require(
            accepted < progress_positions[0],
            f"{label} progressed before acceptance",
        )
        terminals = [
            (index, kind)
            for index, kind in enumerate(kinds)
            if kind in ("completed", "failed", "cancelled")
        ]
        _require(len(terminals) == 1, f"{label} lacks exactly one terminal TASK_RESULT")
        terminal_index, terminal = terminals[0]
        terminal_status = events[terminal_index][1].get("status")
        if terminal == "completed":
            _require(terminal_status == 0, f"{label} completed with nonzero status")
        elif terminal == "failed":
            _require(terminal_status != 0, f"{label} failed with success status")
        else:
            _require(
                terminal_status == AGENT_STATUS_CANCELLED,
                f"{label} cancelled without AGENT_STATUS_CANCELLED",
            )
        _require(assigned < terminal_index, f"{label} terminated before assignment")
        _require(
            all(position < terminal_index for position in progress_positions),
            f"{label} progressed after its terminal state",
        )
        transitions_after_terminal = [
            kind for kind in kinds[terminal_index + 1 :] if kind != "artifact_published"
        ]
        _require(
            not transitions_after_terminal,
            f"{label} emitted a state transition after its terminal state",
        )
        artifact_positions = [
            index for index, kind in enumerate(kinds) if kind == "artifact_published"
        ]
        _require(
            len(artifact_positions) <= 1,
            f"{label} published more than one artifact",
        )
        if artifact_positions:
            _require(
                terminal == "completed",
                f"{label} published an artifact after an unsuccessful terminal state",
            )
            _require(
                artifact_positions[0] > terminal_index,
                f"{label} claimed publication before the worker terminal result",
            )
            _require(
                events[artifact_positions[0]][1].get("task_state") == "completed",
                f"{label} post-terminal artifact is not in completed state",
            )
            _require(
                events[artifact_positions[0]][1].get("status") == 0,
                f"{label} post-terminal artifact has nonzero status",
            )

    assigned_positions = {
        task_key: min(index for index, record in events if record.get("event") == "assigned")
        for task_key, events in grouped.items()
    }
    for task_key, parent_key in parents.items():
        label = _task_label(task_key)
        _require(
            parent_key is None or parent_key in grouped,
            f"{label} references an unknown parent in its turn/request",
        )
        if parent_key is not None and parent_key in grouped:
            child_first = min(index for index, _ in grouped[task_key])
            _require(
                assigned_positions[parent_key] < child_first,
                f"{label} began before parent {_task_label(parent_key)} assignment",
            )
            parent_terminal = next(
                index
                for index, record in grouped[parent_key]
                if record.get("event") in ("completed", "failed", "cancelled")
            )
            child_last = max(index for index, _ in grouped[task_key])
            _require(
                child_last < parent_terminal,
                f"{label} outlived terminal parent {_task_label(parent_key)}",
            )
            parent_assigned = next(
                record
                for _, record in grouped[parent_key]
                if record.get("event") == "assigned"
            )
            child_assigned = next(
                record
                for _, record in grouped[task_key]
                if record.get("event") == "assigned"
            )
            _require(
                (parent_assigned.get("turn_id"), parent_assigned.get("request_id"))
                == (child_assigned.get("turn_id"), child_assigned.get("request_id")),
                f"{label} changed its parent turn/request envelope",
            )
        seen = {task_key}
        cursor = parent_key
        while cursor is not None:
            _require(cursor not in seen, "task parent graph contains a cycle")
            seen.add(cursor)
            cursor = parents.get(cursor)
    _require(any(parent is not None for parent in parents.values()), "Nexus transcript has no delegated task edge")

    def terminal(role: str, event: str, turn_id: int) -> list[tuple[int, int]]:
        return [
            (index, int(record["task_id"]))
            for index, record in enumerate(task_events)
            if record.get("role") == role
            and record.get("event") == event
            and record.get("turn_id") == turn_id
        ]

    system_done = terminal("system", "completed", 1)
    research_done = terminal("research", "completed", 2)
    analyst_done = terminal("analyst", "completed", 3)
    _require(system_done, "turn 1 has no completed System delegation")
    _require(research_done, "turn 2 has no successful Research replan")
    _require(analyst_done, "turn 3 has no completed Analyst delegation")
    research_failed = terminal("research", "failed", 2)
    _require(
        not research_failed,
        "turn 2 invalid Research source reached worker dispatch instead of failing pre-dispatch",
    )


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
    _require(
        research_summary == RESEARCH_EVENT_SUMMARY,
        "Research artifact summary does not match the v4 live_query projection",
    )
    _require(
        digests[1] == RESEARCH_ARTIFACT_SHA256,
        "Research artifact SHA-256 does not bind the complete v4 live_query evidence",
    )
    analyst_summary = "\n".join(str(record.get("summary", "")) for record in analyst)
    expected_analyst_summary = _analyst_event_summary(
        handles[0], handles[1], int(budget_match.group(1))
    )
    _require(
        analyst_summary == expected_analyst_summary,
        "Analyst report event summary does not match its verified handles, core/E2E result, and this-boot budget",
    )
    assigned = [
        record
        for record in task_events
        if record.get("role") == "analyst"
        and record.get("turn_id") == 3
        and record.get("event") == "assigned"
    ]
    _require(len(assigned) == 1, "Analyst report has no unique assigned objective")
    objective = assigned[0].get("summary")
    _require(
        isinstance(objective, str) and bool(objective),
        "Analyst report has no requested focus",
    )
    expected_report = _analyst_report_payload(
        handles[0], handles[1], int(budget_match.group(1)), objective
    )
    expected_report_digest = hashlib.sha256(expected_report.encode("utf-8")).hexdigest()
    _require(
        digests[2] == expected_report_digest,
        "Analyst report SHA-256 does not bind report.v2 finding, actions, validation, rollback, sources, and this-boot budget",
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
    pre_dispatch_failures: list[
        tuple[int, dict[str, object], dict[str, object], dict[str, object]]
    ] = []
    successful_research_replans: list[
        tuple[int, dict[str, object], dict[str, object], dict[str, object]]
    ] = []
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
        if event.get("result") == "task_dispatch_failed;replan_allowed=1":
            _require(
                not children,
                f"delegate_task corr {corr} pre-dispatch failure emitted child TASK events",
            )
            _require(
                event.get("status") == AGENT_STATUS_BAD_PARAM,
                f"delegate_task corr {corr} pre-dispatch failure has the wrong status",
            )
            _require(
                all(event.get(key) == 0 for key in ("value0", "value1", "value2")),
                f"delegate_task corr {corr} pre-dispatch failure reports a nonzero effect",
            )
            _require(
                response.get("turn_id") == 2
                and arguments.get("role") == "research"
                and arguments.get("task_type") == "local_research"
                and _positive_int(arguments.get("input_handle"))
                and not _generation_safe_handle(arguments.get("input_handle")),
                f"delegate_task corr {corr} pre-dispatch failure is not the invalid Research source attempt",
            )
            pre_dispatch_failures.append((corr, response, event, arguments))
            continue
        _require(
            children,
            f"delegate_task corr {corr} lacks child TASK events or the exact pre-dispatch failure result",
        )
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
        assigned_objective = assigned[0].get("summary")
        _require(
            assigned_objective == CANONICAL_DELEGATE_OBJECTIVES.get(str(expected_role)),
            f"delegate_task corr {corr} changed its role-canonical objective",
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
                expected_result in _field_tokens(str(event.get("result", ""))),
                f"delegate_task corr {corr} tool result contradicts its completed role",
            )
            if (
                expected_role == "research"
                and response.get("turn_id") == 2
                and arguments.get("task_type") == "local_research"
                and _generation_safe_handle(arguments.get("input_handle"))
            ):
                successful_research_replans.append((corr, response, event, arguments))
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

    _require(
        len(pre_dispatch_failures) == 1,
        "Nexus does not contain exactly one invalid Research pre-dispatch failure",
    )
    _require(
        len(successful_research_replans) == 1,
        "pre-dispatch failure was not followed by exactly one successful Research replan",
    )
    failed_corr, failed_response, failed_event, failed_arguments = pre_dispatch_failures[0]
    replan_corr, replan_response, replan_event, replan_arguments = successful_research_replans[0]
    _require(
        replan_arguments.get("input_handle") != failed_arguments.get("input_handle")
        and positions[id(failed_event)] < positions[id(replan_response)]
        < positions[id(replan_event)]
        and failed_corr < replan_corr,
        "successful Research replan is not causally after the invalid pre-dispatch attempt",
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
    WorkerTaskMap,
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
    final_answer = str(completions[-1]["answer"])
    final_folded = final_answer.lower()
    _require(
        any(token in final_folded for token in ("denied", "unpublished", "not published")),
        "final turn does not safely report the publication denial",
    )
    _validate_controls(records)

    task_events = _select(records, "type", "task_event")
    _require(task_events, "controller transcript contains no task_event")
    for index, record in enumerate(task_events, 1):
        _validate_task_shape(record, f"task_event {index}")
    kinds = {record.get("event") for record in task_events}
    for required in ("assigned", "accepted", "progress", "completed", "artifact_published"):
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
        _,
        _,
        _,
        _,
        scheduler_facts,
    ) = _validate_artifacts(task_events)
    _validate_fixture_artifact_flow(
        fixture, system_handle, research_handle, analyst_handle
    )
    _require(
        len(scheduler_facts) == 1,
        "System artifact does not expose one canonical scheduler budget",
    )
    _validate_final_answer(final_answer, scheduler_facts[0])
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
        task_key = _task_key(record)
        identity = (
            str(record["role"]),
            int(record["agent_pid"]),
            int(record["agent_id"]),
            int(record["control_id"]),
        )
        previous = worker_tasks.setdefault(task_key, identity)
        _require(previous == identity, f"{_task_label(task_key)} changed worker identity")
    _require(worker_tasks, "controller exposed no delegated worker task identities")
    return str(session_id), identities, worker_tasks


def _validate_observer(
    records: Sequence[dict[str, object]],
    session_id: str,
    controller_identities: set[tuple[str, int, int, int]],
    worker_tasks: Mapping[TaskKey, tuple[str, int, int, int]],
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
    for task_key, expected_identity in worker_tasks.items():
        _require(
            audit_identities.get(expected_identity[1]) == expected_identity,
            f"{_task_label(task_key)} worker identity is not backed by kernel audit",
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
    for task_key, expected_identity in worker_tasks.items():
        raw_task_id = task_key[2]
        label = _task_label(task_key)
        worker_pid = expected_identity[1]
        coordinator_pid = coordinator_identity[1]
        task_audits = [
            record
            for record in message_audits.get(raw_task_id, [])
            if (
                record.get("source_pid") == coordinator_pid
                and record.get("target_pid") == worker_pid
            )
            or (
                record.get("source_pid") == worker_pid
                and record.get("target_pid") == coordinator_pid
            )
        ]
        by_event_id: dict[int, list[dict[str, object]]] = defaultdict(list)
        for record in task_audits:
            by_event_id[int(record["value0"])].append(record)
        pairs: list[tuple[dict[str, object], dict[str, object]]] = []
        for event_id, event_records in by_event_id.items():
            enqueues = [record for record in event_records if record.get("audit_kind") == 2]
            consumes = [record for record in event_records if record.get("audit_kind") == 3]
            _require(
                len(enqueues) == 1 and len(consumes) == 1,
                f"{label} kernel event {event_id} lacks one enqueue/consume pair",
            )
            enqueue, consume = enqueues[0], consumes[0]
            _require(
                int(enqueue["record_sequence"]) < int(consume["record_sequence"]),
                f"{label} kernel event {event_id} was consumed before enqueue",
            )
            _require(
                enqueue.get("source_pid") == consume.get("source_pid")
                and enqueue.get("target_pid") == consume.get("target_pid"),
                f"{label} kernel event {event_id} changed route",
            )
            _require(
                enqueue.get("value1") == raw_task_id
                and consume.get("value1") == raw_task_id,
                f"{label} lost its kernel correlation",
            )
            _require(
                consume.get("loop_state") == 2,
                f"{label} kernel event {event_id} consume did not observe RUNNING",
            )
            pairs.append((enqueue, consume))

        assignments = [
            pair
            for pair in pairs
            if pair[0].get("source_pid") == coordinator_pid
            and pair[0].get("target_pid") == worker_pid
        ]
        _require(assignments, f"{label} has no Coordinator-to-worker MESSAGE pair")
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
                    f"{label} assignment audit identity does not match TASK_EVENT",
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
        _require(responses, f"{label} has no worker-to-Coordinator MESSAGE pair")
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
                    f"{label} response audit identity does not match Coordinator",
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
    for required in ("assigned", "accepted", "progress", "completed", "artifact_published"):
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
                (
                    later.get("turn_id"),
                    later.get("request_id"),
                    later.get("task_id"),
                )
                == (
                    waiting_record.get("turn_id"),
                    waiting_record.get("request_id"),
                    waiting_record.get("task_id"),
                )
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
        _validate_source_evidence_files()
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
