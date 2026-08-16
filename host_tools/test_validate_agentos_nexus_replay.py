#!/usr/bin/env python3
"""Mutation tests for the task-independent Nexus v5 replay validator."""

from __future__ import annotations

import copy
import hashlib
import itertools
from pathlib import Path
import sys
import unittest


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_contract as contract  # noqa: E402
import agentos_nexus_task_ledger as ledger_module  # noqa: E402
import validate_agentos_nexus_replay as validator  # noqa: E402


SESSION = "1" * 32
LIFECYCLE = 77
LIFECYCLE_GENERATION = 3
IDENTITIES = {
    "coordinator": (40, 400, 4000),
    "system": (41, 401, 4001),
    "research": (42, 402, 4002),
}
TOOL_ARGUMENTS = {
    "search_files": {"query": "NexusTaskLedger", "path_prefix": "host_tools/"},
    "read_file": {
        "path": "host_tools/agentos_nexus_task_ledger.py",
        "start_line": 1,
        "max_lines": 3,
    },
    "inspect_system": {"operation": "status"},
}
OBSERVATION_TOOLS = ("search_files", "read_file", "inspect_system")
DEFAULT_GOAL = "Inspect this workspace and summarize the relevant runtime state."
WORKER_NOT_QUIESCENT_SESSION_BLOCK = "worker_not_quiescent;session_blocked=1"


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pair_sha256(user: str, assistant: str) -> str:
    return hashlib.sha256(
        user.encode("utf-8") + b"\0" + assistant.encode("utf-8")
    ).hexdigest()


def _rebuild_observer(
    controller: list[dict[str, object]],
    observer: list[dict[str, object]],
) -> None:
    observer[:] = [
        record for record in observer
        if record.get("event") in ("observer_attached", "kernel_snapshot")
    ]
    for record in controller:
        observer.extend(validator._controller_observer_projection(record))


def _rebind_user_message_index(
    controller: list[dict[str, object]],
    request: dict[str, object],
    index: int,
) -> None:
    request["user_message_index"] = index
    response = next(
        record for record in controller
        if record.get("type") == "model_response"
        and record.get("corr_id") == request["corr_id"]
    )
    response["user_message_index"] = index
    if response.get("response_type") != "final":
        return
    provider_proof = {
        field: response[field]
        for field in validator.MODEL_PROOF_FIELDS
    }
    provider_proof["final_request_sha256"] = response["final_request_sha256"]
    response["provider_proof_sha256"] = validator._sha(provider_proof)
    terminal = next(
        record for record in controller
        if record.get("type") == "turn_complete"
        and record.get("turn_id") == request["turn_id"]
    )
    terminal["provider_proof_sha256"] = response["provider_proof_sha256"]
    terminal["final_proof_root"] = validator._sha(
        {field: terminal[field] for field in validator.FINAL_PROOF_FIELDS}
    )


class Scenario:
    """Build a small replay capture using the production ledger itself."""

    def __init__(
        self,
        *,
        tool_orders: tuple[tuple[str, ...], ...] = (
            ("search_files", "read_file", "inspect_system"),
        ),
        goals: tuple[str, ...] | None = None,
        final_content: str = "The requested workspace inspection is complete.",
        workspace_result: str = "host_tools/example.py:1:sample workspace text",
        max_rounds: int = validator.MAX_ROUNDS,
        failed_tools: frozenset[str] = frozenset(),
        indeterminate_tools: frozenset[str] = frozenset(),
        reset_before_turns: frozenset[int] = frozenset(),
        late_cancel_turns: frozenset[int] = frozenset(),
        final_abort_turns: frozenset[int] = frozenset(),
        final_cleanup_failure_turns: frozenset[int] = frozenset(),
    ) -> None:
        self.goals = goals or tuple(
            DEFAULT_GOAL if index == 0 else f"Follow-up workspace task {index + 1}."
            for index in range(len(tool_orders))
        )
        if len(self.goals) != len(tool_orders):
            raise ValueError("goals and tool_orders must have equal length")
        self.final_content = final_content
        self.workspace_result = workspace_result
        self.max_rounds = max_rounds
        self.failed_tools = failed_tools
        self.indeterminate_tools = indeterminate_tools
        self.reset_before_turns = reset_before_turns
        self.late_cancel_turns = late_cancel_turns
        self.final_abort_turns = final_abort_turns
        self.final_cleanup_failure_turns = final_cleanup_failure_turns
        if not 1 <= max_rounds <= validator.MAX_ROUNDS:
            raise ValueError("max_rounds is outside the Nexus contract")
        if any(len(tools) > max_rounds for tools in tool_orders):
            raise ValueError("tool order exceeds max_rounds")
        if any(not 1 <= turn <= len(tool_orders) for turn in reset_before_turns):
            raise ValueError("reset turn index is outside the scenario")
        if any(not 1 <= turn <= len(tool_orders) for turn in late_cancel_turns):
            raise ValueError("late-cancel turn index is outside the scenario")
        if any(not 1 <= turn <= len(tool_orders) for turn in final_abort_turns):
            raise ValueError("FINAL_ABORT turn index is outside the scenario")
        if any(
            not 1 <= turn <= len(tool_orders)
            for turn in final_cleanup_failure_turns
        ):
            raise ValueError("final cleanup failure turn index is outside the scenario")
        if final_cleanup_failure_turns and final_cleanup_failure_turns != frozenset(
            (len(tool_orders),)
        ):
            raise ValueError("final cleanup failure must block the last scenario turn")
        terminal_failures = final_abort_turns | final_cleanup_failure_turns
        indeterminate_turns = frozenset(
            index
            for index, tools in enumerate(tool_orders, 1)
            if any(tool in indeterminate_tools for tool in tools)
        )
        indeterminate_count = sum(
            tool in indeterminate_tools for tools in tool_orders for tool in tools
        )
        if indeterminate_count != len(indeterminate_tools):
            raise ValueError("each indeterminate tool must occur exactly once")
        if indeterminate_turns and indeterminate_turns != frozenset((len(tool_orders),)):
            raise ValueError("indeterminate worker must block the last scenario turn")
        if indeterminate_turns and final_cleanup_failure_turns:
            raise ValueError("worker and cleanup session blocks cannot overlap")
        if late_cancel_turns.intersection(terminal_failures):
            raise ValueError("late cancel cannot overlap a terminal failure")
        if final_abort_turns.intersection(final_cleanup_failure_turns):
            raise ValueError("FINAL_ABORT and cleanup failure cannot own the same turn")
        if any(
            len(tool_orders[turn - 1]) == max_rounds
            for turn in terminal_failures
        ):
            raise ValueError("round-limited turn cannot reach a frozen-final failure")
        self.controller: list[dict[str, object]] = [
            {"type": "welcome", "role": "controller"},
            {
                "type": "session_ready",
                "session_id": SESSION,
                "max_rounds": max_rounds,
                "max_retries": validator.MAX_RETRIES,
                "provider": "replay",
                "model": "",
                "guest_profile": "nexus",
            },
            {
                "type": "control_result",
                "request_id": 1,
                "command": "tools",
                "status": "ok",
                "result": {"tools": copy.deepcopy(list(contract.TOOLS))},
            },
        ]
        self.fixture: list[dict[str, object]] = []
        self.next_corr = 1
        self.next_task = 1000
        self.next_tool_sequence = 1
        self.next_context_sequence = 1
        self.context_head_sequence = 0
        self.context_branch_generation = 1
        self.context_turns: list[dict[str, object]] = []
        self.active_context_path: dict[str, object] | None = None
        self.next_control_request = 2
        self.tick = 100
        self.generation = 1
        self.ledger = ledger_module.NexusTaskLedger(require_kernel_identity=True)
        for role, (pid, agent_id, control_id) in IDENTITIES.items():
            self.ledger.set_kernel_identity(
                role=role, pid=pid, agent_id=agent_id, control_id=control_id
            )
        for index, (goal, tools) in enumerate(zip(self.goals, tool_orders), 1):
            if index in self.reset_before_turns:
                self._successful_reset()
            self._completed_turn(index, index * 10, goal, tools)
        if self.indeterminate_tools:
            self.controller.append({"type": "session_closed", "reason": "session_error"})
        else:
            self.controller.extend(
                (
                    {"type": "session_closing", "reason": "user_requested"},
                    {
                        "type": "session_closed",
                        "reason": (
                            "session_error"
                            if self.final_cleanup_failure_turns
                            else "guest_complete"
                        ),
                    },
                )
            )
        self.observer = self._observer()

    def _successful_reset(self) -> None:
        self.controller.append(
            {
                "type": "control_result",
                "request_id": self.next_control_request,
                "command": "reset",
                "status": "ok",
                "result": {
                    "count": 0,
                    "oldest_sequence": 0,
                    "latest_sequence": 0,
                    "dropped": 0,
                    "provenance": 0,
                    "detail": "context_and_transcript_cleared",
                },
            }
        )
        self.next_control_request += 1
        self.context_turns.clear()
        self.next_context_sequence = 1
        self.context_head_sequence = 0
        self.context_branch_generation += 1

    def _task_event(
        self,
        turn: int,
        request: int,
        corr: int,
        task_id: int,
        parent: int,
        event: str,
        state: str,
        role: str,
        *,
        status: int = 0,
        **optional: object,
    ) -> dict[str, object]:
        pid, agent_id, control_id = IDENTITIES[role]
        synthetic_session_block = bool(
            parent != 0
            and event == "failed"
            and status == ledger_module.AGENT_STATUS_INDETERMINATE
            and optional.get("summary") == WORKER_NOT_QUIESCENT_SESSION_BLOCK
        )
        route = (
            (pid, pid)
            if parent == 0
            else (IDENTITIES["coordinator"][0], pid)
        )
        value: dict[str, object] = {
            "type": "task_event",
            "turn_id": turn,
            "request_id": request,
            "corr_id": corr,
            "workflow_lifecycle_id": LIFECYCLE,
            "workflow_lifecycle_generation": LIFECYCLE_GENERATION,
            "task_id": task_id,
            "parent_task_id": parent,
            "event": event,
            "task_state": state,
            "role": role,
            "agent_role": role,
            "agent_pid": pid,
            "agent_id": agent_id,
            "control_id_known": True,
            "control_id": control_id,
            "agent_control_id": control_id,
            "source_pid": route[0],
            "target_pid": route[1],
            "status": status,
            "tick": self.tick,
        }
        self.tick += 1
        value.update(optional)
        if parent != 0 and event == "assigned":
            value["summary"] = (
                "task_channel_v1;phase=assigned;channel_generation=7;"
                f"request_id={task_id + 10000};slot_generation={task_id};"
                "tool_id=26;contract_generation=9"
            )
        elif (
            parent != 0
            and event in ledger_module.TASK_TERMINALS
            and not synthetic_session_block
        ):
            value["summary"] = (
                "task_channel_v1;phase=cqe;channel_generation=7;"
                f"request_id={task_id + 10000};slot_generation={task_id};"
                "tool_id=26;contract_generation=9"
            )
            value.setdefault("context_seq", task_id + 1)
        if "digest" in value:
            value["artifact_sha256"] = value["digest"]
        self.ledger.record_event(value)
        self.controller.append(value)
        return value

    def _request(
        self,
        turn: int,
        request: int,
        goal: str,
        corr: int,
        round_number: int,
        history: list[tuple[int, str, str]],
    ) -> dict[str, object]:
        assert self.active_context_path is not None
        value = {
            "type": "model_request",
            "turn_id": turn,
            "request_id": request,
            "corr_id": corr,
            "round": round_number,
            "attempt": round_number,
            "request_sha256": f"{corr:064x}",
            "raw_guest_request_sha256": f"{corr + 10_000:064x}",
            "history_bindings": [
                {
                    "tool_corr_id": prior,
                    "tool": tool,
                    "projection_sha256": digest,
                    "projection_field": (
                        "model_projection"
                        if tool in ledger_module.WORKSPACE_TOOLS
                        else "runtime_observation"
                    ),
                    "data_trust": (
                        ""
                        if tool in ledger_module.WORKSPACE_TOOLS
                        else "guest_runtime_untrusted"
                    ),
                }
                for prior, tool, digest in history[-validator.MAX_HISTORY_BINDINGS:]
            ],
            "context_path": copy.deepcopy(self.active_context_path),
            "request_contains_user": True,
            "user_message_index": 2 * len(self.active_context_path["turns"]),
            "generation": self.generation,
            "user_content_sha256": _sha_text(goal),
            "user_bytes": len(goal.encode("utf-8")),
        }
        self.ledger.record_model_request(corr)
        self.controller.append(value)
        return value

    def _response(
        self,
        request_record: dict[str, object],
        response: dict[str, object],
    ) -> dict[str, object]:
        corr = int(request_record["corr_id"])
        self.fixture.append(
            {
                "request_sha256": request_record["request_sha256"],
                "response": copy.deepcopy(response),
            }
        )
        if response["type"] == "tool_use":
            arguments = copy.deepcopy(response["arguments"])
            self.ledger.record_delivered_tool(
                corr,
                str(response["tool"]),
                arguments_canonical=validator._canonical_bytes(arguments).decode("utf-8"),
            )
            wire = {
                "turn_id": request_record["turn_id"],
                "request_id": request_record["request_id"],
                "corr_id": corr,
                "type": "tool_use",
                "tool": response["tool"],
                "arguments": arguments,
            }
        else:
            self.ledger.freeze_provider_final(corr)
            wire = {
                "turn_id": request_record["turn_id"],
                "request_id": request_record["request_id"],
                "corr_id": corr,
                "type": "final",
                "content": response["content"],
            }
        response_sha = validator._sha(wire)
        proof = {
            "generation": request_record["generation"],
            "provider": "replay",
            "model": "",
            "transport": "replay",
            "adapter_success": True,
            "request_sha256": request_record["request_sha256"],
            "raw_guest_request_sha256": request_record["raw_guest_request_sha256"],
            "history_bindings": copy.deepcopy(request_record["history_bindings"]),
            "request_contains_user": True,
            "user_message_index": request_record["user_message_index"],
            "response_sha256": response_sha,
            "user_content_sha256": request_record["user_content_sha256"],
            "user_bytes": request_record["user_bytes"],
        }
        public = {
            "type": "model_response",
            "turn_id": request_record["turn_id"],
            "request_id": request_record["request_id"],
            "corr_id": corr,
            **proof,
            "response_type": response["type"],
        }
        if response["type"] == "tool_use":
            public.update(
                {
                    "tool": response["tool"],
                    "arguments": copy.deepcopy(response["arguments"]),
                }
            )
        else:
            final_request = str(request_record["request_sha256"])
            provider_proof = {**proof, "final_request_sha256": final_request}
            public.update(
                {
                    "content": response["content"],
                    "final_request_sha256": final_request,
                    "final_response_sha256": response_sha,
                    "provider_proof_sha256": validator._sha(provider_proof),
                }
            )
        self.controller.append(public)
        return public

    def _child_tool(
        self, turn: int, request: int, corr: int, tool: str
    ) -> str:
        task_id = self.next_task
        self.next_task += 1
        role = ledger_module.TASK_TOOL_ROLES[tool]
        _pid, agent_id, _control = IDENTITIES[role]
        deadline = self.tick + 5000
        parent = ledger_module.NEXUS_ROOT_TASK_BASE + turn
        common = dict(
            turn=turn,
            request=request,
            corr=corr,
            task_id=task_id,
            parent=parent,
            role=role,
            deadline_tick=deadline,
        )
        if self.ledger.snapshot().cancelling and tool in (
            "search_files", "read_file"
        ):
            status = -2 if tool in self.failed_tools else -10
            result = "task_failed;replan_allowed=1"
            wrapper = {
                "status": status,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
            }
            event = {
                "type": "tool_event",
                "turn_id": turn,
                "request_id": request,
                "corr_id": corr,
                "tool": tool,
                "status": status,
                "sequence": 0,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
                "context_seq": self.next_tool_sequence + 10,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": validator._sha(wrapper),
                "artifact_sha256": "",
                "data_trust": "untrusted",
                "workspace_source_sha256": "",
                "model_projection": "",
            }
            self.next_tool_sequence += 1
            self.ledger.settle_tool(
                corr,
                tool=tool,
                status=status,
                context_seq=event["context_seq"],
                result_sha256=event["result_sha256"],
            )
            self.controller.append(event)
            return ""
        if tool in ("search_files", "read_file"):
            generation = "d" * 64
            objects_sha256 = "e" * 64
            manifest_content = '{"cursor":0,"entries":[],"eof":true}'
            target_operation = "search" if tool == "search_files" else "read"
            exchanges = (
                (
                    "manifest",
                    1,
                    "",
                    _sha_text('{"cursor":0,"limit":8}'),
                    manifest_content,
                ),
                (
                    target_operation,
                    2,
                    generation,
                    validator._sha(
                        {
                            "tool_arguments_sha256": validator._sha(
                                TOOL_ARGUMENTS[tool]
                            ),
                            "objects_sha256": objects_sha256,
                        }
                    ),
                    self.workspace_result,
                ),
            )
            for operation, attempt, request_generation, arguments_digest, content in exchanges:
                request_event = {
                    "type": "workspace_request",
                    "turn_id": turn,
                    "request_id": request,
                    "corr_id": corr,
                    "task_id": task_id,
                    "tool": tool,
                    "operation": operation,
                    "attempt": attempt,
                    "workspace_generation": request_generation,
                    "arguments_sha256": arguments_digest,
                    "objects_sha256": objects_sha256,
                    "manifest_cursor": 0,
                }
                self.ledger.record_workspace_request(
                    corr,
                    task_id=task_id,
                    tool=tool,
                    operation=operation,
                    attempt=attempt,
                    workspace_generation=request_generation,
                    arguments_sha256=arguments_digest,
                    objects_sha256=objects_sha256,
                    manifest_cursor=0,
                )
                self.controller.append(request_event)
                content_digest = _sha_text(content)
                result_objects_sha256 = (
                    "f" * 64 if operation == "manifest" else objects_sha256
                )
                result_event = {
                    **request_event,
                    "type": "workspace_result",
                    "workspace_generation": generation,
                    "objects_sha256": result_objects_sha256,
                    "status": "ok",
                    "content_bytes": len(content.encode("utf-8")),
                    "content_sha256": content_digest,
                    "manifest_next_cursor": 0,
                    "manifest_eof": operation == "manifest",
                }
                self.ledger.record_workspace_result(
                    corr,
                    task_id=task_id,
                    tool=tool,
                    operation=operation,
                    attempt=attempt,
                    workspace_generation=generation,
                    arguments_sha256=arguments_digest,
                    result_objects_sha256=result_objects_sha256,
                    status="ok",
                    content_bytes=result_event["content_bytes"],
                    content_sha256=content_digest,
                    manifest_cursor=0,
                    manifest_next_cursor=0,
                    manifest_eof=operation == "manifest",
                )
                self.controller.append(result_event)
        self._task_event(**common, event="assigned", state="assigned",
                         summary=f"{tool}_queued;transport=task_channel")
        if tool in self.indeterminate_tools:
            status = ledger_module.AGENT_STATUS_IO_ERROR
            result = "task_failed;replan_allowed=1"
            self._task_event(
                **common,
                event="failed",
                state="failed",
                status=ledger_module.AGENT_STATUS_INDETERMINATE,
                summary=WORKER_NOT_QUIESCENT_SESSION_BLOCK,
            )
            wrapper = {
                "status": status,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
            }
            event = {
                "type": "tool_event",
                "turn_id": turn,
                "request_id": request,
                "corr_id": corr,
                "tool": tool,
                "status": status,
                "sequence": 0,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
                "context_seq": self.next_tool_sequence + 10,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": validator._sha(wrapper),
                "artifact_sha256": "",
                "workspace_source_sha256": "",
                "model_projection": "",
                "data_trust": (
                    "untrusted"
                    if tool in ("search_files", "read_file")
                    else "none"
                ),
            }
            self.next_tool_sequence += 1
            self.ledger.settle_tool(
                corr,
                tool=tool,
                status=status,
                context_seq=event["context_seq"],
                result_sha256=event["result_sha256"],
            )
            self.controller.append(event)
            return ""
        if tool in self.failed_tools:
            status = -2
            result = "task_failed;replan_allowed=1"
            self._task_event(
                **common,
                event="failed",
                state="failed",
                status=status,
                summary=f"{tool}_failed",
            )
            wrapper = {
                "status": status,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
            }
            event = {
                "type": "tool_event",
                "turn_id": turn,
                "request_id": request,
                "corr_id": corr,
                "tool": tool,
                "status": status,
                "sequence": 0,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
                "context_seq": self.next_tool_sequence + 10,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": validator._sha(wrapper),
                "artifact_sha256": "",
                "workspace_source_sha256": "",
                "model_projection": "",
                "data_trust": (
                    "untrusted"
                    if tool in ("search_files", "read_file")
                    else "none"
                ),
            }
            self.next_tool_sequence += 1
            self.ledger.settle_tool(
                corr,
                tool=tool,
                status=status,
                context_seq=event["context_seq"],
                result_sha256=event["result_sha256"],
            )
            self.controller.append(event)
            return ""
        if tool in ("search_files", "read_file"):
            projection = self.workspace_result
            result = validator.WORKSPACE_OBSERVATION_RESULT
            summary = "workspace_observation_ready"
        else:
            projection = validator._system_projection(
                TOOL_ARGUMENTS[tool]["operation"], 5, 2, 0
            )
            result = validator.SYSTEM_OBSERVATION_RESULT
            summary = "system_observation_ready"
        self._task_event(
            **common,
            event="completed",
            state="completed",
            context_seq=self.next_tool_sequence + 9,
        )
        digest = _sha_text(projection)
        self._task_event(
            **common,
            event="artifact_published",
            state="completed",
            provenance=ledger_module.TASK_ARTIFACT_PROVENANCE[tool],
            resource_used=len(projection.encode("utf-8")),
            digest=digest,
            context_seq=self.next_tool_sequence + 10,
            summary=summary,
        )
        values = (5, 2, 0)
        if tool in ("search_files", "read_file"):
            values = (len(projection.encode("utf-8")), task_id, agent_id)
            wrapper = {
                "status": 0,
                "value0": values[0],
                "value1": task_id,
                "value2": agent_id,
                "result": result,
                "model_projection": projection,
            }
        else:
            wrapper = {
                "status": 0,
                "value0": values[0],
                "value1": values[1],
                "value2": values[2],
                "result": result,
                "model_projection": projection,
            }
        event: dict[str, object] = {
            "type": "tool_event",
            "turn_id": turn,
            "request_id": request,
            "corr_id": corr,
            "tool": tool,
            "status": 0,
            "sequence": 0,
            "value0": values[0],
            "value1": values[1],
            "value2": values[2],
            "result": result,
            "context_seq": self.next_tool_sequence + 10,
            "provenance": ledger_module.TOOL_PROVENANCE[tool],
            "projection_sha256": digest,
            "result_sha256": validator._sha(wrapper),
            "artifact_sha256": digest,
            "workspace_source_sha256": "",
            "data_trust": (
                "untrusted"
                if tool in ("search_files", "read_file")
                else "kernel_fact"
            ),
            "model_projection": projection,
        }
        self.next_tool_sequence += 1
        if tool in ("search_files", "read_file"):
            event.update(
                {
                    "workspace_source_sha256": (
                        self.ledger.workspace_source_sha256(corr)
                    ),
                }
            )
        self.ledger.settle_tool(
            corr,
            tool=tool,
            status=0,
            value0=values[0],
            value1=values[1],
            value2=values[2],
            provenance=event["provenance"],
            projection_sha256=digest,
            workspace_source_sha256=event["workspace_source_sha256"],
            context_seq=event["context_seq"],
            result_sha256=event["result_sha256"],
        )
        self.controller.append(event)
        return digest

    def _completed_turn(
        self,
        turn: int,
        request: int,
        goal: str,
        tools: tuple[str, ...],
    ) -> None:
        turn_start_context_head = self.context_head_sequence
        current_user_sequence = self.next_context_sequence
        self.active_context_path = {
            "version": contract.CONTEXT_PATH_VERSION,
            "branch_generation": self.context_branch_generation,
            "visible_head_sequence": current_user_sequence,
            "current_user_sequence": current_user_sequence,
            "turns": copy.deepcopy(
                self.context_turns[-contract.CONTEXT_PATH_MAX_TURNS:]
            ),
        }
        self.ledger.begin_turn(
            turn,
            request,
            workflow_lifecycle_id=LIFECYCLE,
            workflow_lifecycle_generation=LIFECYCLE_GENERATION,
        )
        self.controller.append(
            {
                "type": "turn_started",
                "turn_id": turn,
                "request_id": request,
                "generation": self.generation,
                "user_content_sha256": _sha_text(goal),
                "user_bytes": len(goal.encode("utf-8")),
            }
        )
        corr = self.next_corr
        root = ledger_module.NEXUS_ROOT_TASK_BASE + turn
        self._task_event(
            turn, request, corr, root, 0, "assigned", "assigned", "coordinator",
            summary="user_goal_received",
        )
        self._task_event(
            turn, request, corr, root, 0, "accepted", "accepted", "coordinator"
        )
        self._task_event(
            turn, request, corr, root, 0, "progress", "running", "coordinator",
            metric_code=1, metric_value=8,
        )
        history: list[tuple[int, str, str]] = []
        for round_number, tool in enumerate(tools, 1):
            model_request = self._request(
                turn, request, goal, corr, round_number, history
            )
            self._response(
                model_request,
                {
                    "type": "tool_use",
                    "tool": tool,
                    "arguments": copy.deepcopy(TOOL_ARGUMENTS[tool]),
                },
            )
            digest = self._child_tool(turn, request, corr, tool)
            if (
                round_number == self.max_rounds
                and not self.ledger.snapshot().termination_cause
            ):
                self.ledger.begin_termination(corr, "round_limit")
                if turn in self.late_cancel_turns:
                    self.controller.append(
                        {
                            "type": "turn_cancelling",
                            "turn_id": turn,
                            "request_id": request,
                        }
                    )
            if digest:
                history.append((corr, tool, digest))
            assert self.active_context_path is not None
            self.active_context_path["visible_head_sequence"] = (
                int(self.active_context_path["visible_head_sequence"]) + 1
            )
            if tool in self.indeterminate_tools:
                self._task_event(
                    turn,
                    request,
                    corr,
                    root,
                    0,
                    "failed",
                    "failed",
                    "coordinator",
                    status=ledger_module.AGENT_STATUS_IO_ERROR,
                    summary="turn_failed",
                )
                snapshot = self.ledger.assert_turn_complete("error")
                values = {
                    "version": 1,
                    "turn_id": turn,
                    "request_id": request,
                    "final_corr_id": corr,
                    "final_request_sha256": "",
                    "final_response_sha256": "",
                    "provider_proof_sha256": "",
                    "final_task_root": snapshot.task_root_sha256,
                    "final_artifact_root": snapshot.artifact_root_sha256,
                }
                self.controller.append(
                    {
                        "type": "turn_complete",
                        "turn_id": turn,
                        "request_id": request,
                        "status": "error",
                        "rounds": round_number,
                        "retries": 0,
                        "attempts": round_number,
                        "context_seq": turn_start_context_head,
                        **values,
                        "final_proof_root": validator._sha(values),
                    }
                )
                self.ledger.clear()
                self.generation += 2
                self.next_corr = corr + 1
                self.next_context_sequence = (
                    int(self.active_context_path["visible_head_sequence"]) + 1
                )
                self.active_context_path = None
                return
            corr += 1
        if len(tools) == self.max_rounds:
            terminal_corr = corr - 1
            self._task_event(
                turn,
                request,
                terminal_corr,
                root,
                0,
                "cancelled",
                "cancelled",
                "coordinator",
                status=ledger_module.AGENT_STATUS_CANCELLED,
                summary="turn_cancelled",
            )
            snapshot = self.ledger.assert_turn_complete("cancelled")
            values = {
                "version": 1,
                "turn_id": turn,
                "request_id": request,
                "final_corr_id": terminal_corr,
                "final_request_sha256": "",
                "final_response_sha256": "",
                "provider_proof_sha256": "",
                "final_task_root": snapshot.task_root_sha256,
                "final_artifact_root": snapshot.artifact_root_sha256,
            }
            self.controller.append(
                {
                    "type": "turn_complete",
                    "turn_id": turn,
                    "request_id": request,
                    "status": "cancelled",
                    "rounds": self.max_rounds,
                    "retries": 0,
                    "attempts": self.max_rounds,
                    "context_seq": turn_start_context_head,
                    **values,
                    "final_proof_root": validator._sha(values),
                }
            )
            self.ledger.clear()
            self.generation += 3 if turn in self.late_cancel_turns else 2
            self.next_corr = corr
            assert self.active_context_path is not None
            self.next_context_sequence = (
                int(self.active_context_path["visible_head_sequence"]) + 1
            )
            self.active_context_path = None
            return
        final_round = len(tools) + 1
        model_request = self._request(
            turn, request, goal, corr, final_round, history
        )
        final_response = self._response(
            model_request, {"type": "final", "content": self.final_content}
        )
        if turn in self.late_cancel_turns:
            self.controller.append(
                {
                    "type": "turn_cancelling",
                    "turn_id": turn,
                    "request_id": request,
                }
            )
        if turn in self.final_abort_turns:
            self._task_event(
                turn,
                request,
                corr,
                root,
                0,
                "failed",
                "failed",
                "coordinator",
                status=ledger_module.AGENT_STATUS_NO_SPACE,
                summary=validator.CONTEXT_FINAL_FAILED,
            )
            snapshot = self.ledger.assert_turn_complete("error")
            values = {
                "version": 1,
                "turn_id": turn,
                "request_id": request,
                "final_corr_id": corr,
                "final_request_sha256": final_response["final_request_sha256"],
                "final_response_sha256": final_response["final_response_sha256"],
                "provider_proof_sha256": final_response["provider_proof_sha256"],
                "final_task_root": snapshot.task_root_sha256,
                "final_artifact_root": snapshot.artifact_root_sha256,
            }
            self.controller.append(
                {
                    "type": "turn_complete",
                    "turn_id": turn,
                    "request_id": request,
                    "status": "error",
                    "rounds": final_round,
                    "retries": 0,
                    "attempts": final_round,
                    "context_seq": turn_start_context_head,
                    **values,
                    "final_proof_root": validator._sha(values),
                }
            )
            self.ledger.clear()
            self.generation += 2
            self.next_corr = corr + 1
            assert self.active_context_path is not None
            self.next_context_sequence = (
                int(self.active_context_path["visible_head_sequence"]) + 1
            )
            self.active_context_path = None
            return
        if turn in self.final_cleanup_failure_turns:
            self._task_event(
                turn,
                request,
                corr,
                root,
                0,
                "failed",
                "failed",
                "coordinator",
                status=ledger_module.AGENT_STATUS_IO_ERROR,
                summary=validator.ARTIFACT_CLEANUP_SESSION_BLOCK,
            )
            snapshot = self.ledger.assert_turn_complete("error")
            values = {
                "version": 1,
                "turn_id": turn,
                "request_id": request,
                "final_corr_id": corr,
                "final_request_sha256": final_response["final_request_sha256"],
                "final_response_sha256": final_response["final_response_sha256"],
                "provider_proof_sha256": final_response["provider_proof_sha256"],
                "final_task_root": snapshot.task_root_sha256,
                "final_artifact_root": snapshot.artifact_root_sha256,
            }
            self.controller.append(
                {
                    "type": "turn_complete",
                    "turn_id": turn,
                    "request_id": request,
                    "status": "error",
                    "rounds": final_round,
                    "retries": 0,
                    "attempts": final_round,
                    "context_seq": turn_start_context_head,
                    **values,
                    "final_proof_root": validator._sha(values),
                }
            )
            self.ledger.clear()
            self.generation += 2
            self.next_corr = corr + 1
            self.active_context_path = None
            return
        self._task_event(
            turn, request, corr, root, 0, "completed", "completed", "coordinator"
        )
        snapshot = self.ledger.assert_turn_complete("completed")
        assert self.active_context_path is not None
        final_context_sequence = (
            int(self.active_context_path["visible_head_sequence"]) + 1
        )
        values = {
            "version": 1,
            "turn_id": turn,
            "request_id": request,
            "final_corr_id": corr,
            "final_request_sha256": final_response["final_request_sha256"],
            "final_response_sha256": final_response["final_response_sha256"],
            "provider_proof_sha256": final_response["provider_proof_sha256"],
            "final_task_root": snapshot.task_root_sha256,
            "final_artifact_root": snapshot.artifact_root_sha256,
        }
        self.controller.append(
            {
                "type": "turn_complete",
                "turn_id": turn,
                "request_id": request,
                "status": "completed",
                "answer": self.final_content,
                "rounds": final_round,
                "retries": 0,
                "attempts": final_round,
                "context_seq": final_context_sequence,
                **values,
                "final_proof_root": validator._sha(values),
            }
        )
        self.context_turns.append(
            {
                "turn_id": turn,
                "request_id": request,
                "user_sequence": current_user_sequence,
                "final_sequence": final_context_sequence,
                "sha256": _pair_sha256(goal, self.final_content),
            }
        )
        del self.context_turns[:-contract.CONTEXT_PATH_MAX_TURNS]
        self.ledger.clear()
        self.generation += 3 if turn in self.late_cancel_turns else 2
        self.next_corr = corr + 1
        self.next_context_sequence = final_context_sequence + 1
        self.context_head_sequence = final_context_sequence
        self.active_context_path = None

    def _observer(self) -> list[dict[str, object]]:
        value: list[dict[str, object]] = [
            {
                "type": "telemetry",
                "source": "host",
                "event": "observer_attached",
                "state": "IDLE",
                "turn_id": 0,
                "request_id": 0,
                "session_id": SESSION,
                "guest_profile": "nexus",
            }
        ]
        for index, (role, (pid, agent_id, control_id)) in enumerate(
            IDENTITIES.items(), 1
        ):
            value.append(
                {
                    "type": "telemetry",
                    "source": "kernel_snapshot",
                    "event": "kernel_snapshot",
                    "fresh": False,
                    "tick": 10 + index,
                    "pid": pid,
                    "agent_id": agent_id,
                    "actor_control_id": control_id,
                    "role": role,
                    "workflow_lifecycle_id": LIFECYCLE,
                    "workflow_lifecycle_generation": LIFECYCLE_GENERATION,
                    "loop_state": 2,
                    "capability_mask": 63,
                    "context_seq": index,
                    "wait_sleep_delta": 2,
                    "wait_wakeup_delta": 2,
                    "sched_dispatch": 3,
                    "sched_dispatch_count": 4,
                    "sched_budget": 8,
                    "sched_budget_used": 3,
                    "sched_vruntime": 21,
                }
            )
        for record in self.controller:
            value.extend(validator._controller_observer_projection(record))
        return value


class NexusReplayValidatorTests(unittest.TestCase):
    def validate(self, scenario: Scenario) -> validator.ValidationSummary:
        return validator.validate_records(
            scenario.controller,
            scenario.observer,
            scenario.fixture,
            goals=scenario.goals,
        )

    def rejected(self, scenario: Scenario, mutate) -> None:
        controller = copy.deepcopy(scenario.controller)
        observer = copy.deepcopy(scenario.observer)
        fixture = copy.deepcopy(scenario.fixture)
        mutate(controller, observer, fixture)
        with self.assertRaises(validator.ValidationError):
            validator.validate_records(
                controller, observer, fixture, goals=scenario.goals
            )

    def test_exact_v5_catalog_and_three_identity_runtime(self) -> None:
        self.assertEqual(
            validator.TOOL_NAMES,
            (
                "search_files",
                "read_file",
                "inspect_system",
                "write_file",
                "apply_patch",
                "build_ucore_program",
                "run_ucore_program",
            ),
        )
        self.assertEqual(
            validator.BUSINESS_ROLES,
            frozenset(("coordinator", "system", "research")),
        )
        summary = self.validate(Scenario())
        self.assertEqual(summary.turns[0].status, "completed")
        self.assertEqual(
            tuple(tool for _corr, tool in summary.turns[0].tool_calls),
            OBSERVATION_TOOLS,
        )

    def test_tool_choice_and_order_are_model_owned(self) -> None:
        for order in itertools.permutations(OBSERVATION_TOOLS):
            with self.subTest(order=order):
                summary = self.validate(Scenario(tool_orders=(order,)))
                self.assertEqual(
                    tuple(tool for _corr, tool in summary.turns[0].tool_calls),
                    order,
                )
        repeated = ("read_file", "search_files", "read_file")
        summary = self.validate(Scenario(tool_orders=(repeated,)))
        self.assertEqual(
            tuple(tool for _corr, tool in summary.turns[0].tool_calls), repeated
        )

    def test_history_bindings_accept_only_a_contiguous_latest_suffix(self) -> None:
        settled = [
            (index, "inspect_system", f"{index:064x}")
            for index in range(1, 5)
        ]

        def bindings(correlations: tuple[int, ...]) -> list[dict[str, object]]:
            return [
                {
                    "tool_corr_id": corr_id,
                    "tool": "inspect_system",
                    "projection_sha256": f"{corr_id:064x}",
                    "projection_field": "runtime_observation",
                    "data_trust": "guest_runtime_untrusted",
                }
                for corr_id in correlations
            ]

        for suffix in ((4,), (3, 4), (2, 3, 4), (1, 2, 3, 4)):
            with self.subTest(accepted_suffix=suffix):
                self.assertEqual(
                    validator._history_bindings(
                        bindings(suffix), current_corr=5, settled=settled
                    ),
                    tuple(bindings(suffix)),
                )
        for malformed in ((1, 3, 4), (1, 2, 3), ()):
            with self.subTest(rejected_history=malformed):
                with self.assertRaises(validator.ValidationError):
                    validator._history_bindings(
                        bindings(malformed), current_corr=5, settled=settled
                    )

        for field, replacement in (
            ("projection_field", "model_projection"),
            ("data_trust", ""),
        ):
            malformed = bindings((4,))
            malformed[0][field] = replacement
            with self.subTest(field=field):
                with self.assertRaises(validator.ValidationError):
                    validator._history_bindings(
                        malformed, current_corr=5, settled=settled
                    )

        workspace = [{
            "tool_corr_id": 1,
            "tool": "read_file",
            "projection_sha256": f"{1:064x}",
            "projection_field": "model_projection",
            "data_trust": "",
        }]
        self.assertEqual(
            validator._history_bindings(
                workspace,
                current_corr=2,
                settled=[(1, "read_file", f"{1:064x}")],
            ),
            tuple(workspace),
        )

    def test_zero_or_one_tool_is_a_complete_natural_task(self) -> None:
        direct = self.validate(Scenario(tool_orders=((),)))
        self.assertTrue(direct.turns[0].direct_final)
        for tool in OBSERVATION_TOOLS:
            with self.subTest(tool=tool):
                summary = self.validate(Scenario(tool_orders=((tool,),)))
                self.assertEqual(summary.turns[0].tool_calls[0][1], tool)

    def test_unused_worker_identity_is_not_required(self) -> None:
        scenario = Scenario(tool_orders=((),))
        observer = [
            record
            for record in scenario.observer
            if record.get("source") != "kernel_snapshot"
            or record.get("role") == "coordinator"
        ]
        summary = validator.validate_records(
            scenario.controller,
            observer,
            scenario.fixture,
            goals=scenario.goals,
        )
        self.assertTrue(summary.turns[0].direct_final)

    def test_multiple_natural_tasks_are_accepted(self) -> None:
        scenario = Scenario(
            tool_orders=(("inspect_system",), ("read_file", "search_files")),
            goals=("Inspect current status.", "Read and search this workspace."),
        )
        summary = self.validate(scenario)
        self.assertEqual(len(summary.turns), 2)
        requests = [
            record for record in scenario.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 2
        ]
        self.assertTrue(requests)
        for request in requests:
            path = request["context_path"]
            self.assertEqual(request["user_message_index"], 2)
            self.assertEqual(
                set(path),
                {
                    "version",
                    "branch_generation",
                    "visible_head_sequence",
                    "current_user_sequence",
                    "turns",
                },
            )
            self.assertEqual(len(path["turns"]), 1)
            self.assertEqual(
                path["turns"][0],
                {
                    "turn_id": 1,
                    "request_id": 10,
                    "user_sequence": 1,
                    "final_sequence": 3,
                    "sha256": _pair_sha256(
                        scenario.goals[0], scenario.final_content
                    ),
                },
            )
            self.assertNotIn(scenario.goals[0], repr(path))
            self.assertNotIn(scenario.final_content, repr(path))
        observer_requests = [
            record for record in scenario.observer
            if record.get("event") == "llm_request"
            and record.get("turn_id") == 2
        ]
        self.assertEqual(
            [record["context_path"] for record in observer_requests],
            [record["context_path"] for record in requests],
        )

    def test_terminal_owned_late_cancel_keeps_final_and_fences_next_turn(self) -> None:
        scenario = Scenario(
            tool_orders=((), ()),
            goals=("Finish the first task.", "Use the completed first turn."),
            late_cancel_turns=frozenset((1,)),
        )
        summary = self.validate(scenario)
        self.assertEqual([turn.status for turn in summary.turns], ["completed"] * 2)
        self.assertTrue(summary.turns[0].cancel_requested)
        self.assertFalse(summary.turns[0].cancelled_active_worker)
        self.assertFalse(summary.turns[1].cancel_requested)
        self.assertEqual(
            [
                record["generation"]
                for record in scenario.controller
                if record.get("type") == "turn_started"
            ],
            [1, 4],
        )
        second_request = next(
            record
            for record in scenario.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 2
        )
        self.assertEqual(second_request["user_message_index"], 2)
        self.assertEqual(
            [
                (prior["turn_id"], prior["request_id"])
                for prior in second_request["context_path"]["turns"]
            ],
            [(1, 10)],
        )

    def test_late_cancel_generation_cannot_be_derived_from_terminal_status(self) -> None:
        scenario = Scenario(
            tool_orders=((), ()),
            goals=("Finish before cancellation.", "Start after the cancel fence."),
            late_cancel_turns=frozenset((1,)),
        )

        def erase_cancel_fence(controller, observer, _fixture):
            for record in controller:
                if record.get("turn_id") == 2 and "generation" in record:
                    record["generation"] = 3
            response = next(
                record
                for record in controller
                if record.get("type") == "model_response"
                and record.get("turn_id") == 2
                and record.get("response_type") == "final"
            )
            provider_proof = {
                field: response[field]
                for field in validator.MODEL_PROOF_FIELDS
            }
            provider_proof["final_request_sha256"] = response[
                "final_request_sha256"
            ]
            response["provider_proof_sha256"] = validator._sha(provider_proof)
            terminal = next(
                record
                for record in controller
                if record.get("type") == "turn_complete"
                and record.get("turn_id") == 2
            )
            terminal["provider_proof_sha256"] = response[
                "provider_proof_sha256"
            ]
            terminal["final_proof_root"] = validator._sha(
                {field: terminal[field] for field in validator.FINAL_PROOF_FIELDS}
            )
            _rebuild_observer(controller, observer)

        self.rejected(scenario, erase_cancel_fence)

    def test_context_final_abort_is_a_nonblocking_proved_error(self) -> None:
        scenario = Scenario(
            tool_orders=(("inspect_system",), ()),
            goals=("Attempt a final Context commit.", "Continue after rollback."),
            final_abort_turns=frozenset((1,)),
        )
        summary = self.validate(scenario)
        self.assertEqual([turn.status for turn in summary.turns], ["error", "completed"])
        self.assertEqual(summary.turns[0].final_content, "")
        self.assertFalse(summary.turns[0].cancel_requested)
        self.assertEqual(
            tuple(tool for _corr, tool in summary.turns[0].tool_calls),
            ("inspect_system",),
        )
        failed_root = next(
            record
            for record in scenario.controller
            if record.get("type") == "task_event"
            and record.get("turn_id") == 1
            and record.get("parent_task_id") == 0
            and record.get("event") == "failed"
        )
        self.assertEqual(
            (failed_root["status"], failed_root["summary"]),
            (ledger_module.AGENT_STATUS_NO_SPACE, validator.CONTEXT_FINAL_FAILED),
        )
        final_response = next(
            record
            for record in scenario.controller
            if record.get("type") == "model_response"
            and record.get("turn_id") == 1
            and record.get("response_type") == "final"
        )
        terminal = next(
            record
            for record in scenario.controller
            if record.get("type") == "turn_complete"
            and record.get("turn_id") == 1
        )
        self.assertNotIn("answer", terminal)
        self.assertEqual(terminal["context_seq"], 0)
        self.assertEqual(
            (
                terminal["final_corr_id"],
                terminal["final_request_sha256"],
                terminal["final_response_sha256"],
                terminal["provider_proof_sha256"],
            ),
            (
                final_response["corr_id"],
                final_response["final_request_sha256"],
                final_response["final_response_sha256"],
                final_response["provider_proof_sha256"],
            ),
        )
        second_request = next(
            record
            for record in scenario.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 2
        )
        self.assertEqual(second_request["user_message_index"], 0)
        self.assertEqual(second_request["context_path"]["turns"], [])
        self.assertEqual(
            [
                record["generation"]
                for record in scenario.controller
                if record.get("type") == "turn_started"
            ],
            [1, 3],
        )

    def test_context_final_abort_root_and_turn_mutations_fail(self) -> None:
        scenario = Scenario(
            tool_orders=((),),
            goals=("Fail only the Context FINAL commit.",),
            final_abort_turns=frozenset((1,)),
        )

        def mutate_root(controller, observer, field, value):
            root = next(
                record
                for record in controller
                if record.get("type") == "task_event"
                and record.get("parent_task_id") == 0
                and record.get("event") == "failed"
            )
            root[field] = value
            _rebuild_observer(controller, observer)

        root_mutations = (
            ("summary", "turn_failed"),
            ("status", ledger_module.AGENT_STATUS_IO_ERROR),
            ("corr_id", 2),
        )
        for field, value in root_mutations:
            with self.subTest(root_field=field):
                self.rejected(
                    scenario,
                    lambda controller, observer, _fixture, f=field, v=value:
                    mutate_root(controller, observer, f, v),
                )

        for turn_status in ("completed", "cancelled"):
            with self.subTest(turn_status=turn_status):
                def mutate_turn(
                    controller, observer, _fixture, status=turn_status
                ):
                    terminal = next(
                        record
                        for record in controller
                        if record.get("type") == "turn_complete"
                    )
                    terminal["status"] = status
                    _rebuild_observer(controller, observer)

                self.rejected(scenario, mutate_turn)

    def test_context_final_abort_restores_prior_head_across_sequence_gaps(self) -> None:
        prior_final = Scenario(
            tool_orders=((), ()),
            goals=("Commit the first pair.", "Abort the second FINAL append."),
            final_abort_turns=frozenset((2,)),
        )
        self.assertEqual(
            [turn.status for turn in self.validate(prior_final).turns],
            ["completed", "error"],
        )
        terminals = {
            int(record["turn_id"]): record
            for record in prior_final.controller
            if record.get("type") == "turn_complete"
        }
        self.assertEqual(terminals[2]["context_seq"], terminals[1]["context_seq"])

        def replace_prior_head_with_zero(controller, observer, _fixture):
            terminal = next(
                record
                for record in controller
                if record.get("type") == "turn_complete"
                and record.get("turn_id") == 2
            )
            terminal["context_seq"] = 0
            _rebuild_observer(controller, observer)

        self.rejected(prior_final, replace_prior_head_with_zero)

        gap = Scenario(
            tool_orders=((), ()),
            goals=("Abort once.", "Abort again after the sequence gap."),
            final_abort_turns=frozenset((1, 2)),
        )
        self.assertEqual(
            [turn.status for turn in self.validate(gap).turns],
            ["error", "error"],
        )
        second_request = next(
            record
            for record in gap.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 2
        )
        second_terminal = next(
            record
            for record in gap.controller
            if record.get("type") == "turn_complete"
            and record.get("turn_id") == 2
        )
        self.assertGreater(second_request["context_path"]["current_user_sequence"], 1)
        self.assertEqual(second_terminal["context_seq"], 0)

        def replace_head_with_gap_sequence(controller, observer, _fixture):
            terminal = next(
                record
                for record in controller
                if record.get("type") == "turn_complete"
                and record.get("turn_id") == 2
            )
            terminal["context_seq"] = 1
            _rebuild_observer(controller, observer)

        self.rejected(gap, replace_head_with_gap_sequence)

    def test_frozen_final_cleanup_failure_preserves_proof_and_blocks_session(self) -> None:
        scenario = Scenario(
            tool_orders=(("inspect_system",),),
            goals=("Finish, then fail artifact cleanup.",),
            final_cleanup_failure_turns=frozenset((1,)),
        )
        summary = self.validate(scenario)
        self.assertEqual(summary.turns[0].status, "error")
        self.assertEqual(summary.turns[0].final_content, "")
        self.assertEqual(
            tuple(tool for _corr, tool in summary.turns[0].tool_calls),
            ("inspect_system",),
        )
        self.assertTrue(scenario.ledger.snapshot().session_blocked)
        failed_root = next(
            record
            for record in scenario.controller
            if record.get("type") == "task_event"
            and record.get("parent_task_id") == 0
            and record.get("event") == "failed"
        )
        self.assertEqual(
            (failed_root["status"], failed_root["summary"]),
            (
                ledger_module.AGENT_STATUS_IO_ERROR,
                validator.ARTIFACT_CLEANUP_SESSION_BLOCK,
            ),
        )
        response = next(
            record
            for record in scenario.controller
            if record.get("type") == "model_response"
            and record.get("response_type") == "final"
        )
        terminal = next(
            record
            for record in scenario.controller
            if record.get("type") == "turn_complete"
        )
        self.assertNotIn("answer", terminal)
        self.assertEqual(
            (
                terminal["final_corr_id"],
                terminal["final_request_sha256"],
                terminal["final_response_sha256"],
                terminal["provider_proof_sha256"],
            ),
            (
                response["corr_id"],
                response["final_request_sha256"],
                response["final_response_sha256"],
                response["provider_proof_sha256"],
            ),
        )
        self.assertEqual(scenario.controller[-1]["reason"], "session_error")

    def test_frozen_final_cleanup_failure_mutations_fail(self) -> None:
        scenario = Scenario(
            tool_orders=((),),
            goals=("Reject broadened cleanup-failure acceptance.",),
            final_cleanup_failure_turns=frozenset((1,)),
        )

        def change_cause(controller, observer, _fixture):
            root = next(
                record
                for record in controller
                if record.get("type") == "task_event"
                and record.get("parent_task_id") == 0
                and record.get("event") == "failed"
            )
            root.update(
                {
                    "event": "cancelled",
                    "task_state": "cancelled",
                    "status": ledger_module.AGENT_STATUS_CANCELLED,
                    "summary": "turn_cancelled",
                }
            )
            _rebuild_observer(controller, observer)

        def change_marker(controller, observer, _fixture):
            root = next(
                record
                for record in controller
                if record.get("type") == "task_event"
                and record.get("parent_task_id") == 0
                and record.get("event") == "failed"
            )
            root["summary"] = "artifact_cleanup_failed;session_blocked=0"
            _rebuild_observer(controller, observer)

        def clear_public_session_block(controller, observer, _fixture):
            controller[-1]["reason"] = "guest_complete"
            _rebuild_observer(controller, observer)

        def change_proof(controller, observer, _fixture):
            terminal = next(
                record
                for record in controller
                if record.get("type") == "turn_complete"
            )
            terminal["provider_proof_sha256"] = "0" * 64
            terminal["final_proof_root"] = validator._sha(
                {field: terminal[field] for field in validator.FINAL_PROOF_FIELDS}
            )
            _rebuild_observer(controller, observer)

        mutations = {
            "cause": change_cause,
            "marker": change_marker,
            "session_blocked": clear_public_session_block,
            "proof": change_proof,
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                self.rejected(scenario, mutate)

    def test_worker_session_block_may_close_without_session_closing(self) -> None:
        scenario = Scenario(
            tool_orders=((), ("search_files",)),
            goals=(
                "Commit one durable pair.",
                "Detect a worker that cannot quiesce.",
            ),
            indeterminate_tools=frozenset(("search_files",)),
        )
        summary = self.validate(scenario)
        self.assertEqual([turn.status for turn in summary.turns], ["completed", "error"])
        self.assertTrue(scenario.ledger.snapshot().session_blocked)
        self.assertFalse(
            any(record.get("type") == "session_closing" for record in scenario.controller)
        )
        self.assertEqual(scenario.controller[-1], {
            "type": "session_closed", "reason": "session_error"
        })

        def wrong_reason(controller, observer, _fixture):
            controller[-1]["reason"] = "guest_complete"
            _rebuild_observer(controller, observer)

        self.rejected(scenario, wrong_reason)

        def duplicate_closing(controller, observer, _fixture):
            controller[-1:-1] = [
                {"type": "session_closing", "reason": "user_requested"},
                {"type": "session_closing", "reason": "user_requested"},
            ]
            _rebuild_observer(controller, observer)

        self.rejected(scenario, duplicate_closing)

    def test_three_turn_context_suffix_uses_indices_zero_two_and_four(self) -> None:
        scenario = Scenario(
            tool_orders=((), ("inspect_system",), ("read_file",)),
            goals=("First task.", "Second task.", "Third task."),
        )
        summary = self.validate(scenario)
        self.assertEqual(len(summary.turns), 3)
        indices_by_turn = {
            turn: {
                record["user_message_index"]
                for record in scenario.controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == turn
            }
            for turn in (1, 2, 3)
        }
        self.assertEqual(indices_by_turn, {1: {0}, 2: {2}, 3: {4}})
        third_path = next(
            record["context_path"]
            for record in scenario.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 3
        )
        self.assertEqual(
            [(turn["turn_id"], turn["request_id"]) for turn in third_path["turns"]],
            [(1, 10), (2, 20)],
        )
        self.assertEqual(
            [
                record["context_seq"]
                for record in scenario.controller
                if record.get("type") == "turn_complete"
            ],
            [2, 5, 8],
        )

    def test_same_turn_requests_may_reproject_an_authenticated_suffix(self) -> None:
        scenario = Scenario(
            tool_orders=((), (), ("inspect_system", "read_file")),
            goals=("First.", "Second.", "Use recent context within a small budget."),
        )
        requests = [
            record for record in scenario.controller
            if record.get("type") == "model_request"
            and record.get("turn_id") == 3
        ]
        self.assertEqual(len(requests), 3)
        middle_request = requests[1]
        middle_request["context_path"]["turns"] = []
        _rebind_user_message_index(scenario.controller, middle_request, 0)
        scenario.observer = scenario._observer()
        self.assertEqual(self.validate(scenario).turns[2].status, "completed")
        self.assertEqual(
            [request["user_message_index"] for request in requests],
            [4, 0, 4],
        )

    def test_successful_reset_starts_a_fresh_context_binding_epoch(self) -> None:
        scenario = Scenario(
            tool_orders=((), (), ()),
            goals=("Before reset.", "After reset.", "Continue after reset."),
            reset_before_turns=frozenset((2,)),
        )
        self.assertEqual(len(self.validate(scenario).turns), 3)
        requests = {
            turn: next(
                record for record in scenario.controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == turn
            )
            for turn in (1, 2, 3)
        }
        self.assertEqual(requests[2]["user_message_index"], 0)
        self.assertEqual(requests[2]["context_path"]["turns"], [])
        self.assertEqual(
            [
                (turn["turn_id"], turn["request_id"])
                for turn in requests[3]["context_path"]["turns"]
            ],
            [(2, 20)],
        )
        self.assertNotEqual(
            requests[1]["context_path"]["branch_generation"],
            requests[2]["context_path"]["branch_generation"],
        )

    def test_successful_reset_rejects_resurrected_pre_reset_binding(self) -> None:
        scenario = Scenario(
            tool_orders=((), ()),
            goals=("Before reset.", "After reset."),
            reset_before_turns=frozenset((2,)),
        )

        def resurrect(controller, observer, _fixture):
            request = next(
                record for record in controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == 2
            )
            request["context_path"].update(
                {
                    "visible_head_sequence": 3,
                    "current_user_sequence": 3,
                    "turns": [
                        {
                            "turn_id": 1,
                            "request_id": 10,
                            "user_sequence": 1,
                            "final_sequence": 2,
                            "sha256": _pair_sha256(
                                scenario.goals[0], scenario.final_content
                            ),
                        }
                    ],
                }
            )
            _rebind_user_message_index(controller, request, 2)
            terminal = next(
                record for record in controller
                if record.get("type") == "turn_complete"
                and record.get("turn_id") == 2
            )
            terminal["context_seq"] = 4
            _rebuild_observer(controller, observer)

        self.rejected(scenario, resurrect)

    def test_context_path_index_digest_sequence_order_and_shape_mutations_fail(self) -> None:
        two_turn = Scenario(
            tool_orders=((), ()),
            goals=("Remember this result.", "Use the prior result."),
        )

        def second_request(controller):
            return next(
                record for record in controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == 2
            )

        def mutate_index(controller, observer, _fixture):
            request = second_request(controller)
            _rebind_user_message_index(controller, request, 0)
            _rebuild_observer(controller, observer)

        self.rejected(two_turn, mutate_index)

        def mutate_digest(controller, observer, _fixture):
            second_request(controller)["context_path"]["turns"][0][
                "sha256"
            ] = "f" * 64
            _rebuild_observer(controller, observer)

        self.rejected(two_turn, mutate_digest)

        def mutate_sequence(controller, observer, _fixture):
            second_request(controller)["context_path"]["turns"][0][
                "final_sequence"
            ] = 1
            next(
                record for record in observer
                if record.get("event") == "llm_request"
                and record.get("turn_id") == 2
            )["context_path"]["turns"][0]["final_sequence"] = 1

        self.rejected(two_turn, mutate_sequence)

        three_turn = Scenario(
            tool_orders=((), (), ()),
            goals=("One.", "Two.", "Three."),
        )
        def reverse_prior_turns(controller, observer, _fixture):
            request = next(
                record for record in controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == 3
            )
            request["context_path"]["turns"].reverse()
            next(
                record for record in observer
                if record.get("event") == "llm_request"
                and record.get("turn_id") == 3
            )["context_path"]["turns"].reverse()

        self.rejected(three_turn, reverse_prior_turns)

        def keep_non_suffix_turn(controller, observer, _fixture):
            request = next(
                record for record in controller
                if record.get("type") == "model_request"
                and record.get("turn_id") == 3
            )
            request["context_path"]["turns"] = request[
                "context_path"
            ]["turns"][:1]
            _rebind_user_message_index(controller, request, 2)
            _rebuild_observer(controller, observer)

        self.rejected(three_turn, keep_non_suffix_turn)

        malformed = (
            lambda path: path.__setitem__("version", 2),
            lambda path: path.pop("branch_generation"),
            lambda path: path.__setitem__("turns", {}),
            lambda path: path.__setitem__("messages", []),
        )
        for mutation in malformed:
            with self.subTest(malformed=mutation):
                def mutate_both(
                    controller, observer, _fixture, mutate=mutation
                ):
                    mutate(second_request(controller)["context_path"])
                    observer_request = next(
                        record for record in observer
                        if record.get("event") == "llm_request"
                        and record.get("turn_id") == 2
                    )
                    mutate(observer_request["context_path"])

                self.rejected(two_turn, mutate_both)

    def test_observer_context_path_cannot_carry_message_bodies(self) -> None:
        scenario = Scenario(tool_orders=((), ()))

        def mutate(_controller, observer, _fixture):
            request = next(
                record for record in observer
                if record.get("event") == "llm_request"
                and record.get("turn_id") == 2
            )
            request["context_path"]["turns"][0]["content"] = "prior body"

        self.rejected(scenario, mutate)

    def test_final_content_has_no_business_semantic_gate(self) -> None:
        content = "结论由模型根据当前任务自行给出；协议只验证形状和边界。"
        summary = self.validate(Scenario(final_content=content))
        self.assertEqual(summary.turns[0].final_content, content)

    def test_workspace_bytes_are_bounded_but_not_semantically_interpreted(self) -> None:
        result = "arbitrary/untrusted.txt:9:模型必须自行判断这段内容"
        scenario = Scenario(workspace_result=result)
        self.assertEqual(self.validate(scenario).turns[0].status, "completed")
        workspace_event = next(
            item for item in scenario.controller
            if item.get("type") == "tool_event"
            and item.get("tool") == "search_files"
        )
        self.assertEqual(workspace_event["model_projection"], result)

    def test_workspace_result_uses_raw_utf8_not_json_escaped_size(self) -> None:
        quotes = '"' * 2000
        self.assertEqual(len(quotes.encode("utf-8")), 2000)
        self.assertGreater(
            len(validator._canonical_bytes({"workspace_result": quotes})),
            validator.MAX_WORKSPACE_RESULT_BYTES,
        )
        self.assertEqual(
            self.validate(Scenario(workspace_result=quotes)).turns[0].status,
            "completed",
        )
        for rejected in ("x" * 2801, "bad\0result", "\ud800"):
            with self.subTest(rejected=repr(rejected[:16])):
                with self.assertRaises(validator.ValidationError):
                    validator._bounded_workspace_result(
                        rejected, "Host workspace result"
                    )

    def test_workspace_artifact_settlement_is_exact(self) -> None:
        scenario = Scenario(tool_orders=(("read_file",),))
        mutations = (
            ("result", "workspace_ready"),
            ("value0", 1),
            ("value1", 9999),
            ("value2", IDENTITIES["system"][1]),
            ("provenance", 53),
            ("projection_sha256", "a" * 64),
            ("artifact_sha256", "c" * 64),
            ("workspace_source_sha256", "d" * 64),
            ("model_projection", "changed workspace projection"),
            ("result_sha256", "b" * 64),
            ("data_trust", "trusted"),
            ("context_seq", 9999),
        )
        for field, replacement in mutations:
            with self.subTest(field=field):
                def mutate(controller, _observer, _fixture, f=field, r=replacement):
                    event = next(
                        item for item in controller
                        if item.get("type") == "tool_event"
                    )
                    event[f] = r
                self.rejected(scenario, mutate)

    def test_task_channel_context_routes_and_consumption_order_are_exact(self) -> None:
        scenario = Scenario(tool_orders=(("read_file",),))

        def mutate_completed_route(controller, observer, _fixture):
            completed = next(
                item for item in controller
                if item.get("type") == "task_event"
                and item.get("parent_task_id") != 0
                and item.get("event") == "completed"
            )
            completed["source_pid"] = completed["agent_pid"]
            completed["target_pid"] = IDENTITIES["coordinator"][0]
            _rebuild_observer(controller, observer)

        self.rejected(scenario, mutate_completed_route)

        def mutate_artifact_route(controller, observer, _fixture):
            artifact = next(
                item for item in controller
                if item.get("type") == "task_event"
                and item.get("event") == "artifact_published"
            )
            artifact["source_pid"] = artifact["agent_pid"]
            artifact["target_pid"] = IDENTITIES["coordinator"][0]
            _rebuild_observer(controller, observer)

        self.rejected(scenario, mutate_artifact_route)

        def collapse_consumption_onto_cqe(controller, observer, _fixture):
            completed = next(
                item for item in controller
                if item.get("type") == "task_event"
                and item.get("parent_task_id") != 0
                and item.get("event") == "completed"
            )
            artifact = next(
                item for item in controller
                if item.get("type") == "task_event"
                and item.get("event") == "artifact_published"
            )
            tool = next(
                item for item in controller if item.get("type") == "tool_event"
            )
            artifact["context_seq"] = completed["context_seq"]
            tool["context_seq"] = completed["context_seq"]
            _rebuild_observer(controller, observer)

        self.rejected(scenario, collapse_consumption_onto_cqe)

    def test_system_result_values_projection_and_result_are_exact(self) -> None:
        scenario = Scenario(tool_orders=(("inspect_system",),))
        for field, replacement in (
            ("result", "system_observation_ready;transient=1"),
            ("value0", 6),
            ("value1", 3),
            ("value2", 1),
            ("model_projection", "scope=this_boot_guest_runtime\n"),
        ):
            with self.subTest(field=field):
                def mutate(controller, observer, _fixture, f=field, r=replacement):
                    event = next(
                        item for item in controller
                        if item.get("type") == "tool_event"
                    )
                    event[f] = r
                    wrapper = {
                        "status": event["status"],
                        "value0": event["value0"],
                        "value1": event["value1"],
                        "value2": event["value2"],
                        "result": event["result"],
                        "model_projection": event["model_projection"],
                    }
                    event["result_sha256"] = validator._sha(wrapper)
                    _rebuild_observer(controller, observer)

                self.rejected(scenario, mutate)

    def test_workspace_roundtrip_order_and_body_free_journal_are_exact(self) -> None:
        scenario = Scenario(tool_orders=(("search_files",),))
        exchanges = [
            item for item in scenario.controller
            if item.get("type") in ("workspace_request", "workspace_result")
        ]
        self.assertEqual(
            [(item["type"], item["operation"], item["attempt"]) for item in exchanges],
            [
                ("workspace_request", "manifest", 1),
                ("workspace_result", "manifest", 1),
                ("workspace_request", "search", 2),
                ("workspace_result", "search", 2),
            ],
        )
        self.assertNotEqual(
            exchanges[0]["objects_sha256"], exchanges[1]["objects_sha256"]
        )
        self.assertTrue(
            all(
                not {"arguments", "content", "model_projection"}.intersection(item)
                for item in exchanges
            )
        )
        first_child = next(
            index for index, item in enumerate(scenario.controller)
            if item.get("type") == "task_event"
            and item.get("parent_task_id") != 0
        )
        last_workspace = max(
            index for index, item in enumerate(scenario.controller)
            if item.get("type") == "workspace_result"
        )
        self.assertLess(last_workspace, first_child)
        self.assertFalse(
            any("model_projection" in item for item in scenario.observer)
        )
        self.assertNotIn("host_provider_context", repr(scenario.controller))
        self.assertNotIn("host_workspace_placeholder", repr(scenario.controller))

        def duplicate_result(controller, observer, _fixture):
            index = next(
                i for i, item in enumerate(controller)
                if item.get("type") == "workspace_result"
            )
            controller.insert(index + 1, copy.deepcopy(controller[index]))
            _rebuild_observer(controller, observer)

        self.rejected(scenario, duplicate_result)

        def change_result_objects(controller, observer, _fixture):
            result = next(
                item for item in controller
                if item.get("type") == "workspace_result"
                and item.get("operation") == "manifest"
            )
            result["objects_sha256"] = "a" * 64
            _rebuild_observer(controller, observer)

        self.rejected(scenario, change_result_objects)

        def result_before_request(controller, observer, _fixture):
            request_index = next(
                i for i, item in enumerate(controller)
                if item.get("type") == "workspace_request"
            )
            result_index = request_index + 1
            controller[request_index], controller[result_index] = (
                controller[result_index], controller[request_index]
            )
            _rebuild_observer(controller, observer)

        self.rejected(scenario, result_before_request)

        for field, replacement in (
            ("corr_id", 99999),
            ("task_id", 99999),
            ("arguments_sha256", "a" * 64),
            ("objects_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                def mutate(controller, observer, _fixture, f=field, r=replacement):
                    result = next(
                        item for item in controller
                        if item.get("type") == "workspace_result"
                    )
                    result[f] = r
                    _rebuild_observer(controller, observer)

                self.rejected(scenario, mutate)

        def stale_with_content(controller, observer, _fixture):
            result = next(
                item for item in controller
                if item.get("type") == "workspace_result"
                and item.get("operation") == "search"
            )
            result["status"] = "stale"
            _rebuild_observer(controller, observer)

        self.rejected(scenario, stale_with_content)

        def skip_initial_manifest_cursor(controller, observer, _fixture):
            request = next(
                item for item in controller
                if item.get("type") == "workspace_request"
                and item.get("operation") == "manifest"
            )
            request["manifest_cursor"] = 32
            _rebuild_observer(controller, observer)

        self.rejected(scenario, skip_initial_manifest_cursor)

        def hide_manifest_eof(controller, observer, _fixture):
            result = next(
                item for item in controller
                if item.get("type") == "workspace_result"
                and item.get("operation") == "manifest"
            )
            result["manifest_eof"] = False
            _rebuild_observer(controller, observer)

        self.rejected(scenario, hide_manifest_eof)

        def expose_cursor_on_search(controller, observer, _fixture):
            request = next(
                item for item in controller
                if item.get("type") == "workspace_request"
                and item.get("operation") == "search"
            )
            request["manifest_cursor"] = 1
            _rebuild_observer(controller, observer)

        self.rejected(scenario, expose_cursor_on_search)

    def test_last_round_inspect_success_digest_is_recomputed_without_history(self) -> None:
        scenario = Scenario(
            tool_orders=(("inspect_system",), ()),
            goals=("Inspect once.", "Finish directly."),
            max_rounds=1,
        )
        self.assertEqual(self.validate(scenario).turns[0].status, "cancelled")

        def mutate(controller, _observer, _fixture):
            event = next(
                item
                for item in controller
                if item.get("type") == "tool_event"
            )
            event["result_sha256"] = "f" * 64

        self.rejected(scenario, mutate)

    def test_last_round_workspace_executes_before_round_limit(self) -> None:
        scenario = Scenario(
            tool_orders=(("search_files",), ()),
            goals=("Search once.", "Finish directly."),
            max_rounds=1,
        )
        self.assertEqual(self.validate(scenario).turns[0].status, "cancelled")
        first_turn = [
            item for item in scenario.controller if item.get("turn_id") == 1
        ]
        workspace_result = next(
            index for index, item in enumerate(first_turn)
            if item.get("type") == "workspace_result"
            and item.get("operation") == "search"
        )
        tool_result = next(
            index for index, item in enumerate(first_turn)
            if item.get("type") == "tool_event"
        )
        root_cancel = next(
            index for index, item in enumerate(first_turn)
            if item.get("type") == "task_event"
            and item.get("parent_task_id") == 0
            and item.get("event") == "cancelled"
        )
        self.assertLess(workspace_result, tool_result)
        self.assertLess(tool_result, root_cancel)

    def test_noncompleted_context_sequence_preserves_exact_rollback_head(self) -> None:
        first_turn_cancel = Scenario(
            tool_orders=(("inspect_system",), ()),
            goals=("Cancel the first branch.", "Continue on the rolled-back path."),
            max_rounds=1,
        )
        summary = self.validate(first_turn_cancel)
        self.assertEqual(summary.turns[0].status, "cancelled")
        first_terminal = next(
            record
            for record in first_turn_cancel.controller
            if record.get("type") == "turn_complete"
            and record.get("turn_id") == 1
        )
        self.assertEqual(first_terminal["context_seq"], 0)
        self.assertEqual(
            [
                record["generation"]
                for record in first_turn_cancel.controller
                if record.get("type") == "turn_started"
            ],
            [1, 3],
        )

        prior_completed = Scenario(
            tool_orders=((), ("inspect_system",)),
            goals=("Establish context.", "Cancel back to established context."),
            max_rounds=1,
        )
        self.assertEqual(self.validate(prior_completed).turns[1].status, "cancelled")
        second_terminal = next(
            record
            for record in prior_completed.controller
            if record.get("type") == "turn_complete"
            and record.get("turn_id") == 2
        )
        first_completed = next(
            record
            for record in prior_completed.controller
            if record.get("type") == "turn_complete"
            and record.get("turn_id") == 1
        )
        self.assertEqual(
            second_terminal["context_seq"], first_completed["context_seq"]
        )

        for malformed in (None, -1, 1 << 64):
            with self.subTest(malformed=malformed):
                def mutate(controller, observer, _fixture, value=malformed):
                    terminal = next(
                        record
                        for record in controller
                        if record.get("type") == "turn_complete"
                        and record.get("turn_id") == 1
                    )
                    if value is None:
                        terminal.pop("context_seq")
                    else:
                        terminal["context_seq"] = value
                    _rebuild_observer(controller, observer)

                self.rejected(first_turn_cancel, mutate)

        for not_rolled_back in (1, 2, validator.U64_MAX):
            with self.subTest(not_rolled_back=not_rolled_back):
                def mutate(
                    controller, observer, _fixture, value=not_rolled_back
                ):
                    terminal = next(
                        record
                        for record in controller
                        if record.get("type") == "turn_complete"
                        and record.get("turn_id") == 1
                    )
                    terminal["context_seq"] = value
                    _rebuild_observer(controller, observer)

                self.rejected(first_turn_cancel, mutate)

    def test_last_round_failed_search_digest_is_recomputed_without_history(self) -> None:
        scenario = Scenario(
            tool_orders=(("search_files",), ()),
            goals=("Search once.", "Finish directly."),
            max_rounds=1,
            failed_tools=frozenset(("search_files",)),
        )
        self.assertEqual(self.validate(scenario).turns[0].status, "cancelled")

        def mutate(controller, _observer, _fixture):
            event = next(
                item
                for item in controller
                if item.get("type") == "tool_event"
            )
            event["result_sha256"] = "e" * 64

        self.rejected(scenario, mutate)

    def test_exact_catalog_is_required(self) -> None:
        scenario = Scenario()
        self.rejected(
            scenario,
            lambda controller, _observer, _fixture: controller[2]["result"][
                "tools"
            ].pop(),
        )

    def test_task_role_and_artifact_order_are_bound(self) -> None:
        scenario = Scenario(tool_orders=(("search_files",),))
        self.rejected(
            scenario,
            lambda controller, _observer, _fixture: next(
                item for item in controller
                if item.get("type") == "task_event"
                and item.get("parent_task_id") != 0
            ).update({"role": "system", "agent_role": "system"}),
        )

        def move_artifact(controller, _observer, _fixture):
            index = next(
                i for i, item in enumerate(controller)
                if item.get("type") == "task_event"
                and item.get("event") == "artifact_published"
            )
            artifact = controller.pop(index)
            controller.insert(index - 1, artifact)

        self.rejected(scenario, move_artifact)

    def test_provider_and_terminal_proofs_are_bound(self) -> None:
        scenario = Scenario()
        self.rejected(
            scenario,
            lambda controller, _observer, _fixture: next(
                item for item in controller
                if item.get("type") == "model_response"
                and item.get("response_type") == "final"
            ).update({"provider_proof_sha256": "0" * 64}),
        )
        self.rejected(
            scenario,
            lambda controller, _observer, _fixture: next(
                item for item in controller if item.get("type") == "turn_complete"
            ).update({"final_task_root": "0" * 64}),
        )

    def test_observer_is_projection_only(self) -> None:
        scenario = Scenario()
        self.rejected(
            scenario,
            lambda _controller, observer, _fixture: observer.insert(
                -1,
                {
                    "type": "telemetry",
                    "source": "host",
                    "event": "leak",
                    "content": "private workspace bytes",
                },
            ),
        )
        self.assertFalse(any("workspace_result" in item for item in scenario.observer))

    def test_tool_argument_schemas_have_exact_v4_bounds(self) -> None:
        valid = (
            ("search_files", {"query": "", "path_prefix": ""}),
            ("search_files", {"query": "界" * 95, "path_prefix": "😀" * 111}),
            ("search_files", {"query": '"' * 95}),
            ("read_file", {"path": "x", "start_line": 1, "max_lines": 64}),
            (
                "read_file",
                {"path": "😀" * 255, "start_line": 0xFFFFFFFF, "max_lines": 64},
            ),
            ("inspect_system", {"operation": "context"}),
        )
        for tool, arguments in valid:
            self.assertEqual(
                validator._validate_tool_arguments(tool, arguments), arguments
            )
        invalid = (
            ("search_files", {"query": "x", "extra": 1}),
            ("search_files", {"query": "界" * 96}),
            ("search_files", {"query": "x", "path_prefix": "😀" * 112}),
            ("search_files", {"query": "bad\0query"}),
            ("read_file", {"path": "x", "start_line": 0, "max_lines": 1}),
            (
                "read_file",
                {"path": "😀" * 256, "start_line": 1, "max_lines": 1},
            ),
            (
                "read_file",
                {"path": "x", "start_line": 0x100000000, "max_lines": 1},
            ),
            ("read_file", {"path": "x", "start_line": 1, "max_lines": 65}),
            ("inspect_system", {"operation": "unknown"}),
            ("unknown", {}),
        )
        for tool, arguments in invalid:
            with self.subTest(tool=tool, arguments=arguments):
                with self.assertRaises(validator.ValidationError):
                    validator._validate_tool_arguments(tool, arguments)

        for value in (
            "x" * (validator.MAX_TOOL_ARGUMENT_STRING_BYTES + 1),
            "\x01" * 600,
        ):
            with self.subTest(transport_bytes=len(value.encode("utf-8"))):
                with self.assertRaises(validator.ValidationError):
                    validator._bounded_tool_text(
                        value, "transport boundary", 4000
                    )

    def test_script_accepts_one_or_more_natural_tasks(self) -> None:
        self.assertEqual(
            validator._validate_script_text("/tools\nDo one task.\n/quit\n"),
            ("Do one task.",),
        )
        self.assertEqual(
            validator._validate_script_text(
                "/tools\nFirst task.\n/status\n/agents\nSecond task.\n/quit\n"
            ),
            ("First task.", "Second task."),
        )
        for text in ("/tools\n/quit\n", "Do one task.\n/quit\n"):
            with self.assertRaises(validator.ValidationError):
                validator._validate_script_text(text)

    def test_script_and_fixture_do_not_filter_business_content(self) -> None:
        goal = "Compare 3.118 with 16/16 in this workspace."
        self.assertEqual(
            validator._validate_script_text(f"/tools\n{goal}\n/quit\n"),
            (goal,),
        )
        fixture = validator._validate_fixture(
            [
                {
                    "request_sha256": "a" * 64,
                    "response": {
                        "type": "final",
                        "content": "3.118 is ordinary task data here.",
                    },
                }
            ]
        )
        self.assertEqual(fixture[0].response["content"], "3.118 is ordinary task data here.")

    def test_cli_exposes_workspace_root(self) -> None:
        parser = validator._parser()
        destinations = {action.dest for action in parser._actions}
        self.assertIn("workspace_root", destinations)
        self.assertEqual(
            {"controller", "observer", "fixture", "script", "workspace_root"}
            - destinations,
            set(),
        )


if __name__ == "__main__":
    unittest.main()
