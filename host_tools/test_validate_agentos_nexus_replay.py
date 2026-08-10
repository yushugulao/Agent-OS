#!/usr/bin/env python3
"""Focused self-tests for the AgentOS Nexus replay validator."""

from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import validate_agentos_nexus_replay as validator  # noqa: E402

MEASUREMENT_HANDLE = (1 << 16) | 2
SYSTEM_HANDLE = (1 << 16) | 4
RESEARCH_HANDLE = (1 << 16) | 8
ANALYST_HANDLE = (1 << 16) | 10


def _final_answer() -> str:
    return (
        "publication denied; report remains unpublished;"
        f"system_handle={SYSTEM_HANDLE};research_handle={RESEARCH_HANDLE};"
        "source=nexus_state;claim=this_boot_runtime_observation;"
        "process_count=4;context_count=3;file_bytes=200;"
        "sched_budget=8;"
        f"{validator.SOURCE_REVISION_TOKEN};"
        + ";".join(validator.MEASUREMENT_TOKENS)
    )


def _fixture() -> list[dict[str, object]]:
    responses: list[dict[str, object]] = [
        {"type": "tool_use", "tool": "tool_search", "arguments": {"role": "system", "query": "status"}},
        {"type": "tool_use", "tool": "delegate_task", "arguments": {"role": "system", "task_type": "system_snapshot", "objective": "inspect"}},
        {"type": "final", "content": "system complete"},
        {"type": "tool_use", "tool": "delegate_task", "arguments": {"role": "research", "task_type": "local_research", "objective": "bad", "input_handle": 999}},
        {"type": "tool_use", "tool": "delegate_task", "arguments": {"role": "research", "task_type": "local_research", "objective": "verify", "input_handle": MEASUREMENT_HANDLE}},
        {"type": "tool_use", "tool": "read_artifact", "arguments": {"handle": RESEARCH_HANDLE}},
        {"type": "final", "content": "research replanned"},
        {"type": "tool_use", "tool": "delegate_task", "arguments": {"role": "analyst", "task_type": "compose_report", "objective": "compose", "input_handle": SYSTEM_HANDLE, "secondary_handle": RESEARCH_HANDLE}},
        {"type": "tool_use", "tool": "read_artifact", "arguments": {"handle": ANALYST_HANDLE}},
        {"type": "tool_use", "tool": "publish_report", "arguments": {"handle": ANALYST_HANDLE}},
        {"type": "final", "content": _final_answer()},
    ]
    return [
        {"request_sha256": f"{index:064x}", "response": response}
        for index, response in enumerate(responses, 1)
    ]


def _task_event(
    *,
    turn: int,
    corr: int,
    task: int,
    parent: int,
    event: str,
    state: str,
    role: str,
    pid: int,
    agent: int,
    control: int,
    context: int,
    status: int = 0,
    handle: int | None = None,
    digest: str | None = None,
    summary: str = "",
    resource: int | None = None,
    provenance: int = 1,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "task_event",
        "turn_id": turn,
        "request_id": turn,
        "corr_id": corr,
        "task_id": task,
        "parent_task_id": parent,
        "workflow_lifecycle_id": 1,
        "workflow_lifecycle_generation": 1,
        "event": event,
        "task_state": state,
        "role": role,
        "agent_role": role,
        "agent_pid": pid,
        "agent_id": agent,
        "control_id_known": True,
        "control_id": control,
        "agent_control_id": control,
        "status": status,
        "tick": context * 10,
        "context_seq": context,
        "provenance": provenance,
    }
    if parent != 0:
        record["deadline_tick"] = 10_000 + task
    if handle is not None:
        record["artifact_handle"] = handle
    if digest is not None:
        record["digest"] = digest
        record["artifact_sha256"] = digest
    if summary:
        record["summary"] = summary
    if resource is not None:
        record["resource_used"] = resource
    return record


def _transcripts() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    controller: list[dict[str, object]] = [
        {"type": "session_ready", "session_id": "nexus-test", "guest_profile": "nexus"}
    ]
    turn_for_corr = {**{key: 1 for key in range(1, 4)}, **{key: 2 for key in range(4, 8)}, **{key: 3 for key in range(8, 12)}}
    for corr in range(1, 12):
        turn = turn_for_corr[corr]
        controller.append(
            {
                "type": "model_request",
                "turn_id": turn,
                "request_id": turn,
                "corr_id": corr,
                "request_sha256": f"{corr:064x}",
            }
        )
    for command in ("tools", "status", "context", "context", "context", "status"):
        control: dict[str, object] = {
            "type": "control_result",
            "command": command,
            "status": "ok",
        }
        if command == "status":
            control["result"] = {
                "tick": 100,
                "loop_state": 2,
                "call_count": 4,
                "wait_sleep": 2,
                "wait_wakeup": 2,
                "capability_mask": 0xFF,
            }
        elif command == "context":
            control["result"] = {
                "count": 4,
                "oldest_sequence": 1,
                "latest_sequence": 4,
                "dropped": 0,
                "provenance": 1,
                "detail": "verified Context record",
            }
        controller.append(control)

    task_records: list[dict[str, object]] = []
    sequence = 1

    def emit(**kwargs: object) -> None:
        nonlocal sequence
        task_records.append(_task_event(context=sequence, **kwargs))  # type: ignore[arg-type]
        sequence += 1

    coordinator = dict(role="coordinator", pid=10, agent=100, control=1000)
    system = dict(role="system", pid=11, agent=101, control=1001)
    research = dict(role="research", pid=12, agent=102, control=1002)
    analyst = dict(role="analyst", pid=13, agent=103, control=1003)

    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "running")):
        emit(turn=1, corr=1, task=1, parent=0, event=event, state=state, **coordinator)
    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "waiting"), ("progress", "running")):
        emit(turn=1, corr=2, task=2, parent=1, event=event, state=state, **system)
    emit(turn=1, corr=2, task=2, parent=1, event="completed", state="completed", **system)
    emit(
        turn=1,
        corr=2,
        task=2,
        parent=1,
        event="artifact_published",
        state="completed",
        handle=SYSTEM_HANDLE,
        digest="a" * 64,
        summary=(
            "source=nexus_state;claim=this_boot_runtime_observation;"
            "process_count=4;context_count=3;file_bytes=200;"
            "sched_dispatch_count=8;sched_budget=8;sched_budget_used=3;"
            "sched_vruntime=21"
        ),
        resource=200,
        provenance=53,
        **system,
    )
    emit(turn=1, corr=3, task=1, parent=0, event="completed", state="completed", **coordinator)

    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "running")):
        emit(turn=2, corr=4, task=3, parent=0, event=event, state=state, **coordinator)
    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "running")):
        emit(turn=2, corr=4, task=4, parent=3, event=event, state=state, **research)
    emit(turn=2, corr=4, task=4, parent=3, event="failed", state="failed", status=-5, **research)
    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "waiting"), ("progress", "running")):
        emit(turn=2, corr=5, task=5, parent=3, event=event, state=state, **research)
    emit(turn=2, corr=5, task=5, parent=3, event="completed", state="completed", **research)
    emit(
        turn=2,
        corr=5,
        task=5,
        parent=3,
        event="artifact_published",
        state="completed",
        handle=RESEARCH_HANDLE,
        digest="b" * 64,
        summary=";".join(validator.MEASUREMENT_TOKENS),
        resource=300,
        provenance=60,
        **research,
    )
    emit(turn=2, corr=7, task=3, parent=0, event="completed", state="completed", **coordinator)

    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "running")):
        emit(turn=3, corr=8, task=6, parent=0, event=event, state=state, **coordinator)
    for event, state in (("assigned", "assigned"), ("accepted", "accepted"), ("progress", "waiting"), ("progress", "running")):
        emit(turn=3, corr=8, task=7, parent=6, event=event, state=state, **analyst)
    emit(turn=3, corr=8, task=7, parent=6, event="completed", state="completed", **analyst)
    emit(
        turn=3,
        corr=8,
        task=7,
        parent=6,
        event="artifact_published",
        state="completed",
        handle=ANALYST_HANDLE,
        digest="c" * 64,
        summary=(
            f"system_handle={SYSTEM_HANDLE};research_handle={RESEARCH_HANDLE};"
            f"system_digest={'a' * 64};research_digest={'b' * 64};"
            "sched_budget=8;ratio=4.298"
        ),
        resource=400,
        provenance=61,
        **analyst,
    )
    emit(turn=3, corr=11, task=6, parent=0, event="completed", state="completed", **coordinator)
    controller.extend(task_records)

    approval_arguments = {"handle": ANALYST_HANDLE}
    approval_canonical = validator._canonical_json(approval_arguments)
    approval_digest = hashlib.sha256(approval_canonical.encode("utf-8")).hexdigest()
    controller.extend(
        [
            {"type": "tool_event", "turn_id": 1, "request_id": 1, "corr_id": 1, "tool": "tool_search", "status": 0},
            {"type": "tool_event", "turn_id": 1, "request_id": 1, "corr_id": 2, "tool": "delegate_task", "status": 0, "result": "system_artifact_ready", "value0": SYSTEM_HANDLE, "value1": 2, "value2": 101},
            {"type": "tool_event", "turn_id": 2, "request_id": 2, "corr_id": 4, "tool": "delegate_task", "status": -5, "result": "task_failed;role=research;replan_allowed=1", "value0": 0, "value1": 0},
            {"type": "tool_event", "turn_id": 2, "request_id": 2, "corr_id": 5, "tool": "delegate_task", "status": 0, "result": "research_artifact_ready", "value0": RESEARCH_HANDLE, "value1": 5, "value2": 102},
            {"type": "tool_event", "turn_id": 2, "request_id": 2, "corr_id": 6, "tool": "read_artifact", "status": 0, "value0": RESEARCH_HANDLE},
            {"type": "tool_event", "turn_id": 3, "request_id": 3, "corr_id": 8, "tool": "delegate_task", "status": 0, "result": "analyst_report_ready", "value0": ANALYST_HANDLE, "value1": 7, "value2": 103},
            {"type": "tool_event", "turn_id": 3, "request_id": 3, "corr_id": 9, "tool": "read_artifact", "status": 0, "value0": ANALYST_HANDLE},
            {
                "type": "approval_request",
                "turn_id": 3,
                "request_id": 3,
                "corr_id": 10,
                "tool": "publish_report",
                "tool_id": 1004,
                "arguments": approval_arguments,
                "canonical_arguments": approval_canonical,
                "arguments_sha256": approval_digest,
                "nonce": "nonce",
                "issued_tick": 100,
                "expires_tick": 220,
            },
            {
                "type": "approval_decision",
                "turn_id": 3,
                "request_id": 3,
                "corr_id": 10,
                "tool": "publish_report",
                "tool_id": 1004,
                "arguments_sha256": approval_digest,
                "nonce": "nonce",
                "issued_tick": 100,
                "expires_tick": 220,
                "decision": "deny",
            },
            {
                "type": "tool_event",
                "turn_id": 3,
                "request_id": 3,
                "corr_id": 10,
                "tool": "publish_report",
                "status": -8,
                "result": "not_approved",
                "value0": 0,
                "value1": 0,
                "value2": 0,
            },
            {"type": "turn_complete", "turn_id": 1, "request_id": 1, "status": "completed", "answer": "system complete"},
            {"type": "turn_complete", "turn_id": 2, "request_id": 2, "status": "completed", "answer": "research replanned"},
            {
                "type": "turn_complete",
                "turn_id": 3,
                "request_id": 3,
                "status": "completed",
                "answer": _final_answer(),
            },
            {"type": "session_closed", "session_id": "nexus-test"},
        ]
    )
    unordered = controller
    ready_record = next(record for record in unordered if record.get("type") == "session_ready")
    controls = [record for record in unordered if record.get("type") == "control_result"]
    request_by_corr = {
        int(record["corr_id"]): record
        for record in unordered
        if record.get("type") == "model_request"
    }
    tasks_by_corr: dict[int, list[dict[str, object]]] = {}
    tools_by_corr: dict[int, list[dict[str, object]]] = {}
    for record in unordered:
        if record.get("type") == "task_event":
            tasks_by_corr.setdefault(int(record["corr_id"]), []).append(record)
        elif record.get("type") == "tool_event":
            tools_by_corr.setdefault(int(record["corr_id"]), []).append(record)
    approval_by_corr = {
        int(record["corr_id"]): record
        for record in unordered
        if record.get("type") == "approval_request"
    }
    decision_by_corr = {
        int(record["corr_id"]): record
        for record in unordered
        if record.get("type") == "approval_decision"
    }
    completion_by_turn = {
        int(record["turn_id"]): record
        for record in unordered
        if record.get("type") == "turn_complete"
    }
    controller = [ready_record, *controls]
    for corr, fixture_record in enumerate(_fixture(), 1):
        request = request_by_corr[corr]
        controller.append(request)
        fixture_response = fixture_record["response"]
        assert isinstance(fixture_response, dict)
        response: dict[str, object] = {
            "type": "model_response",
            "turn_id": request["turn_id"],
            "request_id": request["request_id"],
            "corr_id": corr,
            "response_type": fixture_response["type"],
        }
        if fixture_response["type"] == "tool_use":
            response["tool"] = fixture_response["tool"]
            response["arguments"] = copy.deepcopy(fixture_response["arguments"])
        else:
            response["content"] = fixture_response["content"]
        controller.append(response)
        if fixture_response.get("tool") == "delegate_task":
            arguments = fixture_response.get("arguments")
            assert isinstance(arguments, dict)
            for task_event in tasks_by_corr.get(corr, []):
                if (
                    task_event.get("event") == "assigned"
                    and int(task_event.get("parent_task_id", 0)) != 0
                ):
                    task_event["summary"] = arguments["objective"]
        controller.extend(tasks_by_corr.get(corr, []))
        if corr in approval_by_corr:
            controller.append(approval_by_corr[corr])
            controller.append(decision_by_corr[corr])
        controller.extend(tools_by_corr.get(corr, []))
        if fixture_response["type"] == "final":
            controller.append(completion_by_turn[int(request["turn_id"])])
    controller.append(next(record for record in unordered if record.get("type") == "session_closed"))

    for record in controller:
        if record.get("type") != "tool_event":
            continue
        record.setdefault("sequence", 0)
        record.setdefault("value0", 0)
        record.setdefault("value1", 0)
        record.setdefault("value2", 0)
        record.setdefault("result", "verified")
        record.setdefault("context_seq", 100 + int(record["corr_id"]))
        record.setdefault("provenance", 1)

    observer: list[dict[str, object]] = [
        {"type": "telemetry", "event": "observer_attached", "session_id": "nexus-test", "guest_profile": "nexus"},
        {"type": "telemetry", "event": "waiting_llm", "turn_id": 1},
    ]
    audit_sequence = 1

    def audit(
        *,
        kind: int,
        identity: dict[str, object],
        source_pid: int,
        target_pid: int,
        event_type: int,
        value0: int,
        value1: int,
        value2: int,
        loop_state: int,
    ) -> None:
        nonlocal audit_sequence
        observer.append(
            {
                "type": "telemetry",
                "event": "kernel_audit",
                "source": "kernel_audit",
                "fresh": True,
                "record_sequence": audit_sequence,
                "tick": 100 + audit_sequence,
                "audit_kind": kind,
                "workflow_lifecycle_id": 1,
                "workflow_lifecycle_generation": 1,
                "pid": identity["pid"],
                "agent_id": identity["agent"],
                "role": identity["role"],
                "actor_control_id": identity["control"],
                "source_pid": source_pid,
                "target_pid": target_pid,
                "loop_state": loop_state,
                "tool_id": 0,
                "event_type": event_type,
                "status": 0,
                "value0": value0,
                "value1": value1,
                "value2": value2,
                "provenance": 0,
            }
        )
        audit_sequence += 1

    for task_id, worker in ((2, system), (4, research), (5, research), (7, analyst)):
        event_id = task_id + 100
        for kind, loop_state in ((2, 3), (3, 2)):
            audit(
                kind=kind,
                identity=worker,
                source_pid=10,
                target_pid=int(worker["pid"]),
                event_type=2,
                value0=event_id,
                value1=task_id,
                value2=int(worker["pid"]),
                loop_state=loop_state,
            )
        for kind, loop_state in ((2, 3), (3, 2)):
            audit(
                kind=kind,
                identity=coordinator,
                source_pid=int(worker["pid"]),
                target_pid=10,
                event_type=2,
                value0=event_id + 100,
                value1=task_id,
                value2=10,
                loop_state=loop_state,
            )

    for identity in (coordinator, system, research, analyst):
        observer.append(
            {
                "type": "telemetry",
                "event": "kernel_snapshot",
                "source": "kernel_snapshot",
                "fresh": False,
                "tick": 200,
                "pid": identity["pid"],
                "agent_id": identity["agent"],
                "actor_control_id": identity["control"],
                "role": identity["role"],
                "workflow_lifecycle_id": 1,
                "workflow_lifecycle_generation": 1,
                "loop_state": 2,
                "capability_mask": 0xFF,
                "context_seq": 1,
                "wait_sleep_delta": 1,
                "wait_wakeup_delta": 1,
                "sched_dispatch": 1,
                "sched_dispatch_count": 2,
                "sched_budget": 8,
                "sched_budget_used": 1,
                "sched_vruntime": 10,
            }
        )
    for record in task_records:
        telemetry = {key: value for key, value in record.items() if key not in ("type", "summary", "digest")}
        telemetry.update({"type": "telemetry", "source": "guest"})
        observer.append(telemetry)
    for record in controller:
        if record.get("type") != "tool_event":
            continue
        telemetry = {
            key: value
            for key, value in record.items()
            if key not in ("type", "result", "raw", "summary", "content", "objective")
        }
        telemetry.update({"type": "telemetry", "event": "tool_event", "source": "guest"})
        observer.append(telemetry)
    observer.extend(
        [
            {"type": "telemetry", "event": "turn_complete", "turn_id": 1},
            {"type": "telemetry", "event": "turn_complete", "turn_id": 2},
            {"type": "telemetry", "event": "turn_complete", "turn_id": 3},
            {"type": "telemetry", "event": "session_closed"},
        ]
    )
    return controller, observer


class NexusReplayValidatorTests(unittest.TestCase):
    def test_accepts_semantic_golden_transcript(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        digests = validator._fixture_digests(fixture)
        session, identities, worker_tasks = validator._validate_controller(
            controller, digests, fixture
        )
        validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_missing_or_modified_model_response(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        controller.remove(
            next(
                record
                for record in controller
                if record.get("type") == "model_response" and record.get("corr_id") == 1
            )
        )
        with self.assertRaisesRegex(validator.ValidationError, "model_response count"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

        controller, _ = _transcripts()
        response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 1
        )
        response["arguments"] = {"role": "analyst", "query": "status"}
        with self.assertRaisesRegex(validator.ValidationError, "differs from fixture"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_next_request_before_previous_response(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        request_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "model_request" and record.get("corr_id") == 2
        )
        request = controller.pop(request_index)
        response_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "model_response" and record.get("corr_id") == 1
        )
        controller.insert(response_index, request)
        with self.assertRaisesRegex(validator.ValidationError, "overlaps the next request"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_tool_effect_before_model_response(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        tool_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "tool_event" and record.get("corr_id") == 1
        )
        tool = controller.pop(tool_index)
        response_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "model_response" and record.get("corr_id") == 1
        )
        controller.insert(response_index, tool)
        with self.assertRaisesRegex(validator.ValidationError, "response boundary"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_delegate_tool_result_before_task_chain(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        tool_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "tool_event" and record.get("corr_id") == 2
        )
        tool = controller.pop(tool_index)
        first_task_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "task_event" and record.get("corr_id") == 2
        )
        controller.insert(first_task_index, tool)
        with self.assertRaisesRegex(validator.ValidationError, "precedes its TASK"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_request_id_drift_within_a_turn(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        for record in controller:
            if record.get("corr_id") == 6 and record.get("type") in (
                "model_request",
                "model_response",
            ):
                record["request_id"] = 99
        with self.assertRaisesRegex(validator.ValidationError, "not stable within each turn"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_late_session_ready(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        ready = controller.pop(0)
        response_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "model_response"
        )
        controller.insert(response_index + 1, ready)
        with self.assertRaisesRegex(validator.ValidationError, "before session_ready"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_tool_event_attached_to_final_corr(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        event_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "tool_event" and record.get("corr_id") == 1
        )
        event = controller.pop(event_index)
        event["corr_id"] = 3
        completion_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "turn_complete" and record.get("turn_id") == 1
        )
        controller.insert(completion_index, event)
        with self.assertRaisesRegex(validator.ValidationError, "final or unknown"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_delegate_task_with_changed_objective(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        assigned = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("corr_id") == 2
            and record.get("parent_task_id") != 0
            and record.get("event") == "assigned"
        )
        assigned["summary"] = "different objective"
        with self.assertRaisesRegex(validator.ValidationError, "canonical objective"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_delegated_task_envelope_drift(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        accepted = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("task_id") == 2
            and record.get("event") == "accepted"
        )
        accepted["request_id"] = 99
        with self.assertRaisesRegex(
            validator.ValidationError,
            "changed its active turn/request envelope|changed its model response envelope",
        ):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_delegate_tool_result_that_contradicts_terminal(self) -> None:
        fixture = _fixture()
        for field, value, error in (
            ("status", -5, "status contradicts"),
            ("result", "research_artifact_ready", "completed role"),
            ("value0", RESEARCH_HANDLE, "artifact/task"),
        ):
            with self.subTest(field=field):
                controller, _ = _transcripts()
                event = next(
                    record
                    for record in controller
                    if record.get("type") == "tool_event"
                    and record.get("corr_id") == 2
                )
                event[field] = value
                with self.assertRaisesRegex(validator.ValidationError, error):
                    validator._validate_controller(
                        controller, validator._fixture_digests(fixture), fixture
                    )

    def test_rejects_approval_without_exact_canonical_handle_binding(self) -> None:
        fixture = _fixture()
        for field, value, error in (
            ("tool_id", 27, "wrong tool_id"),
            ("arguments", {"handle": SYSTEM_HANDLE}, "Analyst handle"),
            ("canonical_arguments", "{ \"handle\": 1 }", "noncanonical"),
            ("arguments_sha256", "d" * 64, "not recomputed"),
        ):
            with self.subTest(field=field):
                controller, _ = _transcripts()
                request = next(
                    record for record in controller if record.get("type") == "approval_request"
                )
                request[field] = value
                with self.assertRaisesRegex(validator.ValidationError, error):
                    validator._validate_controller(
                        controller, validator._fixture_digests(fixture), fixture
                    )

    def test_rejects_null_approval_or_completion_request_id(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        approval = next(
            record for record in controller if record.get("type") == "approval_request"
        )
        approval["request_id"] = 0
        with self.assertRaisesRegex(validator.ValidationError, "does not match its model response"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

        controller, _ = _transcripts()
        completion = next(
            record
            for record in controller
            if record.get("type") == "turn_complete" and record.get("turn_id") == 2
        )
        completion["request_id"] = 0
        with self.assertRaisesRegex(validator.ValidationError, "active request_id"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_approval_or_close_out_of_raw_order(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        request_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "approval_request"
        )
        decision_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "approval_decision"
        )
        decision = controller.pop(decision_index)
        controller.insert(request_index, decision)
        with self.assertRaisesRegex(validator.ValidationError, "order is invalid"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

        controller, _ = _transcripts()
        controller.append({"type": "notice", "message": "late business output"})
        with self.assertRaisesRegex(validator.ValidationError, "after session_closed"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_empty_fixture_digest(self) -> None:
        fixture = _fixture()
        fixture[0]["request_sha256"] = ""
        with self.assertRaisesRegex(validator.ValidationError, "no valid request_sha256"):
            validator._fixture_digests(fixture)

    def test_fixture_validation_does_not_hardcode_model_request_count(self) -> None:
        fixture = _fixture()
        fixture.insert(
            2,
            {
                "request_sha256": "e" * 64,
                "response": {
                    "type": "tool_use",
                    "tool": "tool_search",
                    "arguments": {"role": "system", "query": "scheduler"},
                },
            },
        )
        self.assertEqual(len(validator._fixture_digests(fixture)), 12)

    def test_rejects_fixture_without_invalid_research_attempt(self) -> None:
        fixture = _fixture()
        response = fixture[3]["response"]
        assert isinstance(response, dict)
        arguments = response["arguments"]
        assert isinstance(arguments, dict)
        arguments["input_handle"] = MEASUREMENT_HANDLE
        with self.assertRaisesRegex(validator.ValidationError, "first Research source"):
            validator._fixture_digests(fixture)

    def test_rejects_publication_of_an_unread_handle(self) -> None:
        fixture = _fixture()
        response = fixture[9]["response"]
        assert isinstance(response, dict)
        arguments = response["arguments"]
        assert isinstance(arguments, dict)
        arguments["handle"] = SYSTEM_HANDLE
        with self.assertRaisesRegex(validator.ValidationError, "different handle"):
            validator._fixture_digests(fixture)

    def test_rejects_analyst_inputs_not_backed_by_returned_artifacts(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        response = fixture[7]["response"]
        assert isinstance(response, dict)
        arguments = response["arguments"]
        assert isinstance(arguments, dict)
        arguments["input_handle"] = MEASUREMENT_HANDLE
        model_response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 8
        )
        model_arguments = model_response["arguments"]
        assert isinstance(model_arguments, dict)
        model_arguments["input_handle"] = MEASUREMENT_HANDLE
        digests = validator._fixture_digests(fixture)
        with self.assertRaisesRegex(validator.ValidationError, "Analyst inputs"):
            validator._validate_controller(controller, digests, fixture)

    def test_rejects_final_without_historical_measurement_evidence(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        final_response = fixture[-1]["response"]
        assert isinstance(final_response, dict)
        final_response["content"] = "publication denied; report remains unpublished"
        final_event = next(
            record
            for record in controller
            if record.get("type") == "turn_complete" and record.get("turn_id") == 3
        )
        final_event["answer"] = final_response["content"]
        model_response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 11
        )
        model_response["content"] = final_response["content"]
        with self.assertRaisesRegex(validator.ValidationError, "final answer omits"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_final_without_dynamic_system_evidence(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        final_response = fixture[-1]["response"]
        assert isinstance(final_response, dict)
        final_response["content"] = str(final_response["content"]).replace(
            "process_count=4;", ""
        )
        final_event = next(
            record
            for record in controller
            if record.get("type") == "turn_complete" and record.get("turn_id") == 3
        )
        final_event["answer"] = final_response["content"]
        model_response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 11
        )
        model_response["content"] = final_response["content"]
        with self.assertRaisesRegex(validator.ValidationError, "dynamic System fact"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_research_artifact_without_historical_scope(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "research"
            and record.get("event") == "artifact_published"
        )
        artifact["summary"] = str(artifact["summary"]).replace(
            ";nexus_derived_measurement_scope=historical_not_this_boot", ""
        )
        with self.assertRaisesRegex(validator.ValidationError, "Research artifact omits"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_prefixed_lookalike_measurement_field(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "research"
            and record.get("event") == "artifact_published"
        )
        artifact["summary"] = str(artifact["summary"]).replace(
            "nexus_derived_checks_basis=wins",
            "fake_nexus_derived_checks_basis=wins",
        )
        with self.assertRaisesRegex(validator.ValidationError, "checks_basis"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_analyst_report_without_full_source_digest(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "analyst"
            and record.get("event") == "artifact_published"
        )
        artifact["summary"] = str(artifact["summary"]).replace(
            f"research_digest={'b' * 64}", "research_digest=missing"
        )
        with self.assertRaisesRegex(validator.ValidationError, "Research digest"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_controller_without_research_handle_readback(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        read = next(
            record
            for record in controller
            if record.get("type") == "tool_event"
            and record.get("tool") == "read_artifact"
            and record.get("value0") == RESEARCH_HANDLE
        )
        read["value0"] = SYSTEM_HANDLE
        with self.assertRaisesRegex(validator.ValidationError, "Research artifact|requested handle"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_denied_publish_side_effect(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        approval = next(record for record in controller if record.get("type") == "approval_request")
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "analyst"
            and record.get("event") == "artifact_published"
        )
        artifact["corr_id"] = approval["corr_id"]
        controller.remove(artifact)
        approval_index = controller.index(approval)
        controller.insert(approval_index, artifact)
        with self.assertRaisesRegex(
            validator.ValidationError,
            "denied publish_report produced|changed its model response envelope",
        ):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_artifact_claim_before_worker_terminal(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "artifact_published"
        )
        artifact = controller.pop(artifact_index)
        terminal_index = next(
            index
            for index, record in enumerate(controller)
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "completed"
        )
        controller.insert(terminal_index, artifact)
        with self.assertRaisesRegex(validator.ValidationError, "before the worker terminal"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_terminal_status_that_disagrees_with_task_state(self) -> None:
        cases = (
            ("system", "completed", -5, "completed with nonzero status"),
            ("research", "failed", 0, "failed with success status"),
            ("research", "cancelled", -9, "AGENT_STATUS_CANCELLED"),
        )
        for role, terminal, status, error in cases:
            with self.subTest(terminal=terminal):
                controller, _ = _transcripts()
                source_event = "failed" if terminal == "cancelled" else terminal
                record = next(
                    item
                    for item in controller
                    if item.get("type") == "task_event"
                    and item.get("role") == role
                    and item.get("event") == source_event
                )
                record["event"] = terminal
                record["task_state"] = terminal
                record["status"] = status
                with self.assertRaisesRegex(validator.ValidationError, error):
                    validator._validate_task_dag(
                        [
                            item
                            for item in controller
                            if item.get("type") == "task_event"
                        ]
                    )

    def test_rejects_task_that_changes_agent_identity(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "artifact_published"
        )
        artifact["agent_id"] = 999
        with self.assertRaisesRegex(validator.ValidationError, "changed Agent identity"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_artifact_without_resource_accounting(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("event") == "artifact_published"
        )
        artifact["resource_used"] = 0
        with self.assertRaisesRegex(validator.ValidationError, "resource-account"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_lost_cross_agent_provenance(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        system = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "artifact_published"
        )
        system["provenance"] = validator.PROVENANCE_KERNEL_FACT
        with self.assertRaisesRegex(validator.ValidationError, "System artifact lost"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

        controller, _ = _transcripts()
        research = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "research"
            and record.get("event") == "artifact_published"
        )
        research["provenance"] = 52
        with self.assertRaisesRegex(validator.ValidationError, "file/tool/cross-Agent"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

        controller, _ = _transcripts()
        analyst = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "analyst"
            and record.get("event") == "artifact_published"
        )
        analyst["provenance"] = 60
        with self.assertRaisesRegex(validator.ValidationError, "both input provenance"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_system_artifact_without_scheduler_fact(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        system = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "artifact_published"
        )
        system["summary"] = str(system["summary"]).replace(";sched_vruntime=21", "")
        with self.assertRaisesRegex(validator.ValidationError, "sched_vruntime"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_system_artifact_without_scheduler_budget(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        system = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "system"
            and record.get("event") == "artifact_published"
        )
        system["summary"] = str(system["summary"]).replace("sched_budget=8;", "")
        with self.assertRaisesRegex(validator.ValidationError, "sched_budget"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_analyst_report_without_scheduler_budget(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        analyst = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "analyst"
            and record.get("event") == "artifact_published"
        )
        analyst["summary"] = str(analyst["summary"]).replace(
            "sched_budget=8;", ""
        )
        with self.assertRaisesRegex(validator.ValidationError, "scheduler budget"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_final_without_scheduler_budget(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        final_response = fixture[-1]["response"]
        assert isinstance(final_response, dict)
        final_response["content"] = str(final_response["content"]).replace(
            "sched_budget=8;", ""
        )
        final_event = next(
            record
            for record in controller
            if record.get("type") == "turn_complete" and record.get("turn_id") == 3
        )
        final_event["answer"] = final_response["content"]
        model_response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 11
        )
        model_response["content"] = final_response["content"]
        with self.assertRaisesRegex(validator.ValidationError, "scheduler fact"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_artifact_slot_reuse_or_regression(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        artifact = next(
            record
            for record in controller
            if record.get("type") == "task_event"
            and record.get("role") == "research"
            and record.get("event") == "artifact_published"
        )
        artifact["artifact_handle"] = (1 << 16) | 3
        tool = next(
            record
            for record in controller
            if record.get("type") == "tool_event" and record.get("corr_id") == 5
        )
        tool["value0"] = artifact["artifact_handle"]
        with self.assertRaisesRegex(validator.ValidationError, "aliased or reused"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_artifact_generation_that_disagrees_with_lifecycle(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        for record in controller:
            if record.get("type") == "task_event":
                record["workflow_lifecycle_generation"] = 2
        with self.assertRaisesRegex(
            validator.ValidationError,
            "artifact handle generation does not match",
        ):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_fixture_handle_from_another_lifecycle_generation(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        response = fixture[4]["response"]
        assert isinstance(response, dict)
        arguments = response["arguments"]
        assert isinstance(arguments, dict)
        arguments["input_handle"] = (2 << 16) | 2
        model_response = next(
            record
            for record in controller
            if record.get("type") == "model_response" and record.get("corr_id") == 5
        )
        model_response["arguments"] = copy.deepcopy(arguments)
        with self.assertRaisesRegex(
            validator.ValidationError,
            "crossed the workflow lifecycle generation",
        ):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_status_without_capability_evidence(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        for record in controller:
            if record.get("type") == "control_result" and record.get("command") == "status":
                result = record.get("result")
                assert isinstance(result, dict)
                result["capability_mask"] = 0
        with self.assertRaisesRegex(validator.ValidationError, "capability mask"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_audit_sequence_masquerading_as_context(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        audit = next(record for record in observer if record.get("source") == "kernel_audit")
        audit["context_seq"] = audit["record_sequence"]
        with self.assertRaisesRegex(validator.ValidationError, "confuses audit sequence"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_kernel_identity_not_backed_by_audit(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        audit = next(
            record
            for record in observer
            if record.get("source") == "kernel_audit" and record.get("role") == "system"
        )
        audit["actor_control_id"] = int(audit["actor_control_id"]) + 1
        with self.assertRaisesRegex(validator.ValidationError, "kernel_audit|kernel audit"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_synthetic_message_audit_provenance(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        audit = next(
            record
            for record in observer
            if record.get("source") == "kernel_audit"
            and record.get("audit_kind") in (2, 3)
        )
        audit["provenance"] = 1
        with self.assertRaisesRegex(validator.ValidationError, "zero/unavailable"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_non_message_kernel_audit(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        audit = next(
            record
            for record in observer
            if record.get("event") == "kernel_audit"
        )
        audit["audit_kind"] = 1
        with self.assertRaisesRegex(validator.ValidationError, "non-MESSAGE kind"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_snapshot_without_control_or_capability_binding(self) -> None:
        fixture = _fixture()
        for field, value, error in (
            ("actor_control_id", 9999, "identity disagrees"),
            ("capability_mask", 0, "capability evidence"),
        ):
            with self.subTest(field=field):
                controller, observer = _transcripts()
                session, identities, worker_tasks = validator._validate_controller(
                    controller, validator._fixture_digests(fixture), fixture
                )
                snapshot = next(
                    record
                    for record in observer
                    if record.get("event") == "kernel_snapshot"
                )
                snapshot[field] = value
                with self.assertRaisesRegex(validator.ValidationError, error):
                    validator._validate_observer(
                        observer, session, identities, worker_tasks
                    )

    def test_rejects_busy_poll_snapshot_without_sleep_delta(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        snapshot = next(record for record in observer if record.get("source") == "kernel_snapshot")
        snapshot["wait_sleep_delta"] = 0
        with self.assertRaisesRegex(validator.ValidationError, "no nonbusy wait"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_resume_observed_before_wait(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        first_waiting = next(
            record
            for record in observer
            if record.get("event") in validator.TASK_EVENTS
            and record.get("task_state") == "waiting"
        )
        task_id = first_waiting["task_id"]
        waiting = [
            record
            for record in observer
            if record.get("event") in validator.TASK_EVENTS
            and record.get("task_id") == task_id
            and record.get("task_state") == "waiting"
        ]
        observer[:] = [record for record in observer if record not in waiting]
        last_task_position = max(
            index
            for index, record in enumerate(observer)
            if record.get("event") in validator.TASK_EVENTS
            and record.get("task_id") == task_id
        )
        observer[last_task_position + 1 : last_task_position + 1] = waiting
        with self.assertRaisesRegex(
            validator.ValidationError,
            "does not exactly match|resume.*strictly after",
        ):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_observer_task_projection_rewrite_or_loss(self) -> None:
        fixture = _fixture()
        for mutation in ("rewrite", "drop", "order"):
            with self.subTest(mutation=mutation):
                controller, observer = _transcripts()
                session, identities, worker_tasks = validator._validate_controller(
                    controller, validator._fixture_digests(fixture), fixture
                )
                if mutation == "rewrite":
                    for record in observer:
                        if record.get("event") in validator.TASK_EVENTS:
                            record["task_id"] = 999999
                else:
                    if mutation == "drop":
                        observer[:] = [
                            record
                            for record in observer
                            if not (
                                record.get("event") in validator.TASK_EVENTS
                                and record.get("task_id") == 2
                            )
                        ]
                    else:
                        positions = [
                            index
                            for index, record in enumerate(observer)
                            if record.get("event") in validator.TASK_EVENTS
                        ]
                        observer[positions[0]], observer[positions[1]] = (
                            observer[positions[1]],
                            observer[positions[0]],
                        )
                with self.assertRaisesRegex(validator.ValidationError, "does not exactly match"):
                    validator._validate_observer(
                        observer, session, identities, worker_tasks
                    )

    def test_rejects_pseudo_tool_schema_ranges_and_provenance(self) -> None:
        fixture = _fixture()
        mutations = (
            ("unknown", "fields do not match"),
            ("sequence", "sequence=0"),
            ("context", "Context evidence"),
            ("provenance", "unknown provenance"),
            ("status", "invalid status"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                controller, _ = _transcripts()
                tool = next(
                    record
                    for record in controller
                    if record.get("type") == "tool_event"
                    and record.get("status") == 0
                )
                if mutation == "unknown":
                    tool["unexpected"] = 1
                elif mutation == "sequence":
                    tool["sequence"] = 1
                elif mutation == "context":
                    tool["context_seq"] = 0
                elif mutation == "provenance":
                    tool["provenance"] = validator.PROVENANCE_ALL + 1
                else:
                    tool["status"] = validator.MAX_I32 + 1
                with self.assertRaisesRegex(validator.ValidationError, message):
                    validator._validate_controller(
                        controller, validator._fixture_digests(fixture), fixture
                    )

    def test_rejects_delegate_tool_identity_mismatch(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        delegate = next(
            record
            for record in controller
            if record.get("type") == "tool_event"
            and record.get("tool") == "delegate_task"
            and record.get("status") == 0
        )
        delegate["value2"] = int(delegate["value2"]) + 1
        with self.assertRaisesRegex(validator.ValidationError, "artifact/task/Agent"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_task_unknown_fields_and_wire_range_overflow(self) -> None:
        fixture = _fixture()
        mutations = (
            ("unknown", "unexpected fields"),
            ("tick", "invalid tick"),
            ("task", "invalid task_id"),
            ("parent", "invalid parent_task_id"),
            ("status", "invalid status"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                controller, _ = _transcripts()
                task = next(record for record in controller if record.get("type") == "task_event")
                if mutation == "unknown":
                    task["unexpected"] = 1
                elif mutation == "tick":
                    task["tick"] = validator.MAX_WIRE_U64 + 1
                elif mutation == "task":
                    task["task_id"] = validator.MAX_U32 + 1
                elif mutation == "parent":
                    task["parent_task_id"] = validator.MAX_U32 + 1
                else:
                    task["status"] = validator.MAX_I32 + 1
                with self.assertRaisesRegex(validator.ValidationError, message):
                    validator._validate_controller(
                        controller, validator._fixture_digests(fixture), fixture
                    )

    def test_rejects_child_that_begins_before_parent_assignment(self) -> None:
        fixture = _fixture()
        controller, _ = _transcripts()
        for record in controller:
            if record.get("type") == "task_event" and record.get("task_id") == 2:
                record["parent_task_id"] = 6
        with self.assertRaisesRegex(validator.ValidationError, "before parent 6 assignment"):
            validator._validate_controller(
                controller, validator._fixture_digests(fixture), fixture
            )

    def test_rejects_task_without_accept_and_progress(self) -> None:
        controller, _ = _transcripts()
        task_events = [
            record
            for record in controller
            if record.get("type") == "task_event"
            and not (
                record.get("task_id") == 2
                and record.get("event") in ("accepted", "progress")
            )
        ]
        with self.assertRaisesRegex(validator.ValidationError, "TASK_ACCEPT"):
            validator._validate_task_dag(task_events)

    def test_rejects_child_after_parent_terminal(self) -> None:
        controller, _ = _transcripts()
        task_events = [
            copy.deepcopy(record)
            for record in controller
            if record.get("type") == "task_event"
        ]
        terminal_index = next(
            index
            for index, record in enumerate(task_events)
            if record.get("task_id") == 3 and record.get("event") == "completed"
        )
        terminal = task_events.pop(terminal_index)
        terminal["corr_id"] = 4
        child_index = next(
            index
            for index, record in enumerate(task_events)
            if record.get("task_id") == 4 and record.get("event") == "assigned"
        )
        task_events.insert(child_index, terminal)
        with self.assertRaisesRegex(validator.ValidationError, "outlived terminal parent 3"):
            validator._validate_task_dag(task_events)

    def test_rejects_missing_past_or_drifting_task_deadline(self) -> None:
        mutations = (
            ("missing", "lacks its delegated-task deadline"),
            ("past", "deadline is not in the future"),
            ("drift", "changed its delegated-task deadline"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                controller, _ = _transcripts()
                task_events = [
                    copy.deepcopy(record)
                    for record in controller
                    if record.get("type") == "task_event"
                ]
                child = [record for record in task_events if record.get("task_id") == 2]
                if mutation == "missing":
                    child[-1].pop("deadline_tick")
                elif mutation == "past":
                    deadline = int(child[0]["tick"])
                    for record in child:
                        record["deadline_tick"] = deadline
                else:
                    child[-1]["deadline_tick"] = int(child[-1]["deadline_tick"]) + 1
                with self.assertRaisesRegex(validator.ValidationError, message):
                    validator._validate_task_dag(task_events)

    def test_rejects_observer_kernel_unknown_fields_and_range_overflow(self) -> None:
        fixture = _fixture()
        mutations = (
            ("audit_unknown", "kernel_audit.*fields"),
            ("audit_status", "kernel_audit.*status"),
            ("snapshot_unknown", "kernel_snapshot.*fields"),
            ("snapshot_tick", "kernel_snapshot.*tick"),
            ("snapshot_loop", "kernel_snapshot.*loop_state"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                controller, observer = _transcripts()
                session, identities, worker_tasks = validator._validate_controller(
                    controller, validator._fixture_digests(fixture), fixture
                )
                audit = next(record for record in observer if record.get("event") == "kernel_audit")
                snapshot = next(record for record in observer if record.get("event") == "kernel_snapshot")
                if mutation == "audit_unknown":
                    audit["unexpected"] = 1
                elif mutation == "audit_status":
                    audit["status"] = validator.MAX_I32 + 1
                elif mutation == "snapshot_unknown":
                    snapshot["unexpected"] = 1
                elif mutation == "snapshot_tick":
                    snapshot["tick"] = validator.MAX_WIRE_U64 + 1
                else:
                    snapshot["loop_state"] = validator.MAX_U32 + 1
                with self.assertRaisesRegex(validator.ValidationError, message):
                    validator._validate_observer(
                        observer, session, identities, worker_tasks
                    )

    def test_rejects_observer_task_or_tool_schema_extensions(self) -> None:
        fixture = _fixture()
        for event, message in (("assigned", "task_event.*unexpected"), ("tool_event", "fields do not match")):
            with self.subTest(event=event):
                controller, observer = _transcripts()
                session, identities, worker_tasks = validator._validate_controller(
                    controller, validator._fixture_digests(fixture), fixture
                )
                record = next(item for item in observer if item.get("event") == event)
                record["unexpected"] = 1
                with self.assertRaisesRegex(validator.ValidationError, message):
                    validator._validate_observer(
                        observer, session, identities, worker_tasks
                    )

    def test_rejects_observer_tool_projection_rewrite_or_loss(self) -> None:
        fixture = _fixture()
        for mutation in ("drop", "corr", "status"):
            with self.subTest(mutation=mutation):
                controller, observer = _transcripts()
                session, identities, worker_tasks = validator._validate_controller(
                    controller, validator._fixture_digests(fixture), fixture
                )
                tools = [
                    record
                    for record in observer
                    if record.get("event") == "tool_event"
                    and record.get("source") == "guest"
                ]
                self.assertTrue(tools)
                if mutation == "drop":
                    observer.remove(tools[0])
                elif mutation == "corr":
                    tools[0]["corr_id"] = int(tools[0]["corr_id"]) + 100
                else:
                    tools[0]["status"] = -999
                with self.assertRaisesRegex(
                    validator.ValidationError,
                    "observer tool metadata does not exactly match",
                ):
                    validator._validate_observer(
                        observer, session, identities, worker_tasks
                    )

    def test_rejects_observer_telemetry_after_session_close(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        closed = observer.pop()
        observer.insert(1, closed)
        with self.assertRaisesRegex(validator.ValidationError, "after session_closed"):
            validator._validate_observer(observer, session, identities, worker_tasks)

    def test_rejects_controller_summary_leaked_to_observer(self) -> None:
        fixture = _fixture()
        controller, observer = _transcripts()
        session, identities, worker_tasks = validator._validate_controller(
            controller, validator._fixture_digests(fixture), fixture
        )
        task = next(record for record in observer if record.get("event") == "artifact_published")
        task["summary"] = "must stay on the controller"
        with self.assertRaisesRegex(validator.ValidationError, "controller-only fields"):
            validator._validate_observer(observer, session, identities, worker_tasks)


if __name__ == "__main__":
    unittest.main()
