#!/usr/bin/env python3
"""Mutation tests for the task-independent Nexus v3 replay validator."""

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
DEFAULT_GOAL = "Inspect this workspace and summarize the relevant runtime state."


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        if not 1 <= max_rounds <= validator.MAX_ROUNDS:
            raise ValueError("max_rounds is outside the Nexus contract")
        if any(len(tools) > max_rounds for tools in tool_orders):
            raise ValueError("tool order exceeds max_rounds")
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
        self.tick = 100
        self.generation = 1
        self.ledger = ledger_module.NexusTaskLedger(require_kernel_identity=True)
        for role, (pid, agent_id, control_id) in IDENTITIES.items():
            self.ledger.set_kernel_identity(
                role=role, pid=pid, agent_id=agent_id, control_id=control_id
            )
        for index, (goal, tools) in enumerate(zip(self.goals, tool_orders), 1):
            self._completed_turn(index, index * 10, goal, tools)
        self.controller.extend(
            (
                {"type": "session_closing", "reason": "user_requested"},
                {"type": "session_closed", "reason": "guest_complete"},
            )
        )
        self.observer = self._observer()

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
        route = (
            (pid, pid)
            if parent == 0
            else ((IDENTITIES["coordinator"][0], pid) if event == "assigned"
                  else (pid, IDENTITIES["coordinator"][0]))
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
                }
                for prior, tool, digest in history[-validator.MAX_HISTORY_BINDINGS:]
            ],
            "request_contains_user": True,
            "user_message_index": 0,
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
            "user_message_index": 0,
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
        self._task_event(**common, event="assigned", state="assigned",
                         summary=f"{tool}_assigned")
        self._task_event(**common, event="accepted", state="accepted")
        self._task_event(**common, event="progress", state="running", context_seq=7)
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
            }
            self.next_tool_sequence += 1
            self.ledger.settle_tool(
                corr,
                tool=tool,
                status=status,
                result_sha256=event["result_sha256"],
            )
            self.controller.append(event)
            return ""
        if tool in ("search_files", "read_file"):
            projection = (
                f"workspace_request={tool}\n"
                "result_delivery=host_provider_context\n"
                "content_untrusted=1\n"
            )
            result = validator.WORKSPACE_REQUEST_RESULT
            summary = "workspace_request_ready"
        else:
            system_values = (5, 2, 0)
            result_metrics: dict[int, int] = {}
            for index, value in enumerate(system_values):
                low_code = validator.RESULT_VALUE_METRIC_FIRST + index * 2
                result_metrics[low_code] = value & 0xFFFFFFFF
                result_metrics[low_code + 1] = value >> 32
            for metric_code, metric_value in result_metrics.items():
                self._task_event(
                    **common,
                    event="progress",
                    state="running",
                    metric_code=metric_code,
                    metric_value=metric_value,
                )
            projection = validator._inspect_system_projection(
                TOOL_ARGUMENTS[tool]["operation"], result_metrics
            )
            result = "system_observation_ready;transient=1"
            summary = "system_observation_ready"
        self._task_event(**common, event="completed", state="completed")
        digest = _sha_text(projection)
        self._task_event(
            **common,
            event="artifact_published",
            state="completed",
            provenance=ledger_module.TASK_ARTIFACT_PROVENANCE[tool],
            resource_used=len(projection.encode("utf-8")),
            digest=digest,
            summary=summary,
        )
        values = (0, task_id, agent_id)
        if tool in ("search_files", "read_file"):
            wrapper = {
                "status": 0,
                "value0": 0,
                "value1": task_id,
                "value2": agent_id,
                "result": result,
                "workspace_request": projection,
                "data_trust": "host_workspace_placeholder",
            }
        else:
            wrapper = {
                "status": 0,
                "value0": 0,
                "value1": task_id,
                "value2": agent_id,
                "result": result,
                "runtime_observation": projection,
                "data_trust": "guest_runtime_untrusted",
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
        }
        self.next_tool_sequence += 1
        if tool in ("search_files", "read_file"):
            event.update(
                {
                    "workspace_result": self.workspace_result,
                    "data_trust": "host_workspace_untrusted",
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
            if round_number == self.max_rounds:
                self.ledger.begin_termination(corr, "round_limit")
            digest = self._child_tool(turn, request, corr, tool)
            if digest:
                history.append((corr, tool, digest))
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
                    **values,
                    "final_proof_root": validator._sha(values),
                }
            )
            self.ledger.clear()
            self.generation += 3
            self.next_corr = corr
            return
        final_round = len(tools) + 1
        model_request = self._request(
            turn, request, goal, corr, final_round, history
        )
        final_response = self._response(
            model_request, {"type": "final", "content": self.final_content}
        )
        self._task_event(
            turn, request, corr, root, 0, "completed", "completed", "coordinator"
        )
        snapshot = self.ledger.assert_turn_complete("completed")
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
                **values,
                "final_proof_root": validator._sha(values),
            }
        )
        self.ledger.clear()
        self.generation += 2
        self.next_corr = corr + 1

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

    def test_exact_v3_catalog_and_three_identity_runtime(self) -> None:
        self.assertEqual(
            validator.TOOL_NAMES,
            ("search_files", "read_file", "inspect_system"),
        )
        self.assertEqual(
            validator.BUSINESS_ROLES,
            frozenset(("coordinator", "system", "research")),
        )
        summary = self.validate(Scenario())
        self.assertEqual(summary.turns[0].status, "completed")
        self.assertEqual(
            tuple(tool for _corr, tool in summary.turns[0].tool_calls),
            validator.TOOL_NAMES,
        )

    def test_tool_choice_and_order_are_model_owned(self) -> None:
        for order in itertools.permutations(validator.TOOL_NAMES):
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

    def test_zero_or_one_tool_is_a_complete_natural_task(self) -> None:
        direct = self.validate(Scenario(tool_orders=((),)))
        self.assertTrue(direct.turns[0].direct_final)
        for tool in validator.TOOL_NAMES:
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
        self.assertEqual(workspace_event["workspace_result"], result)

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

    def test_workspace_placeholder_settlement_is_exact(self) -> None:
        scenario = Scenario(tool_orders=(("read_file",),))
        mutations = (
            ("result", "workspace_ready"),
            ("value0", 1),
            ("value1", 9999),
            ("value2", IDENTITIES["system"][1]),
            ("provenance", 53),
            ("projection_sha256", "a" * 64),
            ("result_sha256", "b" * 64),
            ("data_trust", "trusted"),
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

    def test_tool_argument_schemas_have_exact_v3_bounds(self) -> None:
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
