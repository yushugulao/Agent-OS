#!/usr/bin/env python3
"""Mutation tests built from the real Nexus Guest TASK/TOOL ordering."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sys
import unittest


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_task_ledger as ledger


TURN = 7
REQUEST = 19
LIFECYCLE = 0x1234
GENERATION = 3
ROOT = ledger.NEXUS_ROOT_TASK_BASE + TURN
CORR1 = 10001
CORR2 = 10002
CORR3 = 10003
ROOT_ID = ("coordinator", 40, 400, 4000)
SYSTEM_ID = ("system", 41, 401, 4001)
RESEARCH_ID = ("research", 42, 402, 4002)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64

TOOL_SPEC = {
    "search_files": ('{"query":"nexus_root_start","path_prefix":"user/"}', RESEARCH_ID, 60, 60),
    "read_file": ('{"max_lines":64,"path":"user/src/init.c","start_line":40}', RESEARCH_ID, 60, 60),
    "inspect_system": ('{"operation":"status"}', SYSTEM_ID, 53, 53),
}


class NexusTaskLedgerTests(unittest.TestCase):
    def make(self, *, identities: bool = True) -> ledger.NexusTaskLedger:
        value = ledger.NexusTaskLedger(require_kernel_identity=identities)
        value.begin_turn(
            TURN,
            REQUEST,
            workflow_lifecycle_id=LIFECYCLE,
            workflow_lifecycle_generation=GENERATION,
        )
        if identities:
            for role, pid, agent_id, control_id in (
                ROOT_ID,
                SYSTEM_ID,
                RESEARCH_ID,
            ):
                value.set_kernel_identity(
                    role=role,
                    pid=pid,
                    agent_id=agent_id,
                    control_id=control_id,
                )
        return value

    def test_development_final_requires_current_build_and_three_guest_cases(self) -> None:
        value = self.make()
        self.root_prelude(value)
        value._record_development_result(
            "write_file",
            SHA_A,
            f"workspace_write\nrevision={SHA_A}\n",
        )
        self.rejected(lambda: value.freeze_provider_final(CORR1))
        value._record_development_result(
            "build_ucore_program",
            SHA_A,
            f"ucore_build\nstatus=failed\nsource_revision={SHA_A}\n",
        )
        self.rejected(lambda: value.freeze_provider_final(CORR1))
        value._record_development_result(
            "build_ucore_program",
            SHA_A,
            (
                "ucore_build\nstatus=passed\n"
                f"source_revision={SHA_A}\nbuild_id={SHA_B}\n"
            ),
        )
        for case_kind in ("normal", "invalid"):
            value._record_development_result(
                "run_ucore_program",
                SHA_A,
                (
                    "ucore_run\nstatus=passed\n"
                    f"source_revision={SHA_A}\nbuild_id={SHA_B}\n"
                    f"case_kind={case_kind}\n"
                ),
            )
        self.rejected(lambda: value.freeze_provider_final(CORR1))
        value._record_development_result(
            "run_ucore_program",
            SHA_A,
            (
                "ucore_run\nstatus=passed\n"
                f"source_revision={SHA_A}\nbuild_id={SHA_B}\n"
                "case_kind=failure\n"
            ),
        )
        value.freeze_provider_final(CORR1)

    def event(
        self,
        *,
        task_id: int = ROOT,
        parent_task_id: int = 0,
        corr_id: int = CORR1,
        kind: str = "assigned",
        state: str | None = None,
        identity: tuple[str, int, int, int] = ROOT_ID,
        status: int = 0,
        tick: int = 10,
        deadline_tick: int = 0,
        source_pid: int | None = None,
        target_pid: int | None = None,
        **optional: object,
    ) -> dict[str, object]:
        states = {
            "assigned": "assigned",
            "accepted": "accepted",
            "progress": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "artifact_published": "completed",
        }
        role, pid, agent_id, control_id = identity
        if source_pid is None or target_pid is None:
            if parent_task_id == 0:
                route = (ROOT_ID[1], ROOT_ID[1])
            else:
                route = (ROOT_ID[1], pid)
            if source_pid is None:
                source_pid = route[0]
            if target_pid is None:
                target_pid = route[1]
        result: dict[str, object] = {
            "turn_id": TURN,
            "request_id": REQUEST,
            "corr_id": corr_id,
            "workflow_lifecycle_id": LIFECYCLE,
            "workflow_lifecycle_generation": GENERATION,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "event": kind,
            "task_state": states[kind] if state is None else state,
            "role": role,
            "agent_pid": pid,
            "agent_id": agent_id,
            "control_id_known": True,
            "control_id": control_id,
            "status": status,
            "tick": tick,
            "source_pid": source_pid,
            "target_pid": target_pid,
        }
        if parent_task_id != 0:
            phase = "assigned" if kind == "assigned" else "cqe"
            if kind == "assigned" or kind in ledger.TASK_TERMINALS:
                result["summary"] = (
                    f"task_channel_v1;phase={phase};channel_generation=7;"
                    f"request_id={task_id + 10000};slot_generation={task_id};"
                    "tool_id=26;contract_generation=9"
                )
            if kind in ledger.TASK_TERMINALS:
                result["context_seq"] = task_id + tick
            elif kind == "artifact_published":
                result["context_seq"] = task_id + tick
        if deadline_tick:
            result["deadline_tick"] = deadline_tick
        result.update(optional)
        return result

    def root_prelude(
        self,
        value: ledger.NexusTaskLedger,
        *,
        corr_id: int = CORR1,
        request: bool = True,
    ) -> None:
        value.record_event(
            self.event(corr_id=corr_id, summary="user_goal_received")
        )
        value.record_event(
            self.event(corr_id=corr_id, kind="accepted", tick=11)
        )
        value.record_event(
            self.event(
                corr_id=corr_id,
                kind="progress",
                tick=12,
                metric_code=1,
                metric_value=8,
            )
        )
        if request:
            value.record_model_request(corr_id)

    def deliver(self, value: ledger.NexusTaskLedger, corr: int, tool: str) -> str:
        arguments, _identity, _provenance, _artifact_provenance = TOOL_SPEC[tool]
        value.record_delivered_tool(corr, tool, arguments_canonical=arguments)
        return arguments

    def workspace_content(
        self,
        value: ledger.NexusTaskLedger,
        *,
        corr: int = CORR1,
        task_id: int = 1000,
        tool: str = "search_files",
        digest: str = SHA_A,
        resource: int = 64,
    ) -> None:
        arguments = TOOL_SPEC[tool][0]
        generation = "d" * 64
        manifest_arguments = hashlib.sha256(
            b'{"cursor":0,"limit":8}'
        ).hexdigest()
        target_arguments = hashlib.sha256(arguments.encode("utf-8")).hexdigest()
        objects = "e" * 64
        value.record_workspace_request(
            corr,
            task_id=task_id,
            tool=tool,
            operation="manifest",
            attempt=1,
            workspace_generation="",
            arguments_sha256=manifest_arguments,
            objects_sha256=objects,
            manifest_cursor=0,
        )
        value.record_workspace_result(
            corr,
            task_id=task_id,
            tool=tool,
            operation="manifest",
            attempt=1,
            workspace_generation=generation,
            arguments_sha256=manifest_arguments,
            result_objects_sha256=objects,
            status="ok",
            content_bytes=128,
            content_sha256=SHA_C,
            manifest_cursor=0,
            manifest_next_cursor=1,
            manifest_eof=True,
        )
        operation = "search" if tool == "search_files" else "read"
        value.record_workspace_request(
            corr,
            task_id=task_id,
            tool=tool,
            operation=operation,
            attempt=2,
            workspace_generation=generation,
            arguments_sha256=target_arguments,
            objects_sha256=objects,
            manifest_cursor=0,
        )
        value.record_workspace_result(
            corr,
            task_id=task_id,
            tool=tool,
            operation=operation,
            attempt=2,
            workspace_generation=generation,
            arguments_sha256=target_arguments,
            result_objects_sha256=objects,
            status="ok",
            content_bytes=resource,
            content_sha256=digest,
            manifest_cursor=0,
            manifest_next_cursor=0,
            manifest_eof=False,
        )

    def child_success(
        self,
        value: ledger.NexusTaskLedger,
        *,
        corr: int = CORR1,
        task_id: int = 1000,
        tool: str = "search_files",
        digest: str | None = None,
        settle: bool = True,
    ) -> tuple[str, int, int]:
        arguments = self.deliver(value, corr, tool)
        _args, identity, provenance, artifact_provenance = TOOL_SPEC[tool]
        handle = 0
        resource = 64
        if tool in ("search_files", "read_file"):
            digest = SHA_A if digest is None else digest
            self.workspace_content(
                value,
                corr=corr,
                task_id=task_id,
                tool=tool,
                digest=digest,
                resource=resource,
            )
        if digest is None:
            digest = SHA_A
        deadline = 5000
        common = dict(
            task_id=task_id,
            parent_task_id=ROOT,
            corr_id=corr,
            identity=identity,
            deadline_tick=deadline,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(self.event(**common, kind="completed", tick=23))
        value.record_event(
            self.event(
                **common,
                kind="artifact_published",
                tick=24,
                artifact_handle=handle,
                provenance=artifact_provenance,
                resource_used=resource,
                digest=digest,
                summary="workspace_observation_ready"
                if tool in ("search_files", "read_file")
                else "system_observation_ready",
            )
        )
        if settle:
            value0 = resource if tool in ("search_files", "read_file") else handle
            value.settle_tool(
                corr,
                tool=tool,
                status=0,
                value0=value0,
                value1=task_id,
                value2=identity[2],
                provenance=provenance,
                projection_sha256=digest,
                context_seq=task_id + 24,
                workspace_source_sha256=(
                    value.workspace_source_sha256(corr)
                    if tool in ("search_files", "read_file")
                    else ""
                ),
                result_sha256=SHA_B,
            )
        return digest, handle, resource

    def final(
        self, value: ledger.NexusTaskLedger, corr: int
    ) -> ledger.NexusTaskLedgerSnapshot:
        value.record_model_request(corr)
        value.freeze_provider_final(corr)
        value.record_event(
            self.event(corr_id=corr, kind="completed", tick=200)
        )
        return value.assert_turn_complete("completed")

    def rejected(self, call) -> None:
        with self.assertRaises(ledger.NexusTaskLedgerError):
            call()

    def test_workspace_trace_seals_deterministically_without_bodies(self) -> None:
        roots: list[tuple[str, str]] = []
        for _ in range(2):
            value = self.make()
            self.root_prelude(value)
            self.child_success(value)
            snapshot = self.final(value, CORR2)
            self.assertTrue(snapshot.all_required_terminal)
            self.assertEqual(snapshot.model_request_count, 2)
            self.assertEqual(snapshot.latest_corr_id, CORR2)
            self.assertRegex(snapshot.task_root_sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(snapshot.artifact_root_sha256, r"^[0-9a-f]{64}$")
            self.assertNotIn("user_goal_received", repr(value.__dict__))
            self.assertNotIn("nexus_root_start", repr(value.__dict__))
            roots.append((snapshot.task_root_sha256, snapshot.artifact_root_sha256))
        self.assertEqual(roots[0], roots[1])
        with self.assertRaises(FrozenInstanceError):
            snapshot.turn_id = 1  # type: ignore[misc]

    def test_corr_fsm_requires_exact_root_prelude_and_one_outcome(self) -> None:
        value = self.make()
        self.rejected(lambda: value.record_model_request(CORR1))

        value = self.make()
        self.root_prelude(value, request=False)
        self.rejected(lambda: value.record_model_request(CORR2))
        value.record_model_request(CORR1)
        self.rejected(
            lambda: value.record_delivered_tool(
                CORR2,
                "search_files",
                arguments_canonical=TOOL_SPEC["search_files"][0],
            )
        )
        self.rejected(lambda: value.record_model_request(CORR2))
        self.deliver(value, CORR1, "search_files")
        self.rejected(lambda: value.freeze_provider_final(CORR1))
        self.rejected(lambda: value.record_model_request(CORR2))

        value = self.make()
        self.root_prelude(value, request=False)
        value.record_event(
            self.event(kind="progress", tick=13, metric_code=1, metric_value=9)
        ) if False else None
        # Extra root progress is rejected before it can enter a proof.
        self.rejected(
            lambda: value.record_event(
                self.event(kind="progress", tick=13, metric_code=1, metric_value=9)
            )
        )

    def test_retryable_error_is_a_closed_request_outcome(self) -> None:
        value = self.make()
        self.root_prelude(value)
        value.record_model_error(CORR1, retryable=True)
        value.record_model_request(CORR2)
        value.freeze_provider_final(CORR2)
        value.record_event(self.event(corr_id=CORR2, kind="completed", tick=40))
        self.assertTrue(value.assert_turn_complete("completed").sealed)

    def test_exact_termination_contracts_reject_cross_combinations(self) -> None:
        cases = (
            ("provider_fatal", "failed", -18, "error"),
            ("round_limit", "cancelled", -10, "cancelled"),
            ("user_interrupt", "cancelled", -10, "cancelled"),
        )
        for cause, event, status, turn_status in cases:
            with self.subTest(cause=cause):
                value = self.make()
                self.root_prelude(value)
                if cause == "provider_fatal":
                    value.record_model_error(CORR1, retryable=False)
                elif cause == "round_limit":
                    value.record_model_error(CORR1, retryable=True)
                value.begin_termination(CORR1, cause)
                value.record_event(
                    self.event(kind=event, status=status, tick=40)
                )
                self.rejected(
                    lambda v=value, wrong=("error" if turn_status == "cancelled" else "cancelled"):
                    v.assert_turn_complete(wrong)
                )
                self.assertTrue(value.assert_turn_complete(turn_status).sealed)

        value = self.make()
        self.root_prelude(value)
        self.rejected(lambda: value.begin_termination(CORR2, "round_limit"))

    def test_context_final_failure_binds_the_frozen_final_to_error(self) -> None:
        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        value.record_event(
            self.event(
                kind="failed",
                status=ledger.AGENT_STATUS_NO_SPACE,
                tick=40,
                summary="context_final_failed",
                context_seq=13,
            )
        )

        staged = value.snapshot()
        self.assertTrue(staged.provider_final_frozen)
        self.assertTrue(staged.cancelling)
        self.assertEqual(staged.termination_cause, "context_final_failed")
        self.assertFalse(staged.session_blocked)
        self.assertEqual(value._requests[CORR1].response_outcome, "final")
        self.assertEqual(
            value._requests[CORR1].termination_cause, "context_final_failed"
        )
        self.assertEqual(staged.tasks[0].terminal_event, "failed")
        self.assertEqual(
            staged.tasks[0].terminal_status, ledger.AGENT_STATUS_NO_SPACE
        )
        self.rejected(lambda: value.assert_turn_complete("completed"))
        self.rejected(lambda: value.assert_turn_complete("cancelled"))
        sealed = value.assert_turn_complete("error")
        self.assertTrue(sealed.sealed)
        self.assertFalse(sealed.session_blocked)
        value.clear()
        value.begin_turn(TURN + 1, REQUEST + 1)
        self.assertTrue(value.active)

    def test_context_final_failure_rejects_envelope_and_owner_mutations(self) -> None:
        mutations = (
            {"status": ledger.AGENT_STATUS_IO_ERROR},
            {"summary": "context_final_failure"},
            {"corr_id": CORR2},
            {"task_state": "cancelled"},
            {
                "event": "cancelled",
                "status": ledger.AGENT_STATUS_CANCELLED,
                "task_state": "cancelled",
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                value.freeze_provider_final(CORR1)
                event = self.event(
                    kind="failed",
                    status=ledger.AGENT_STATUS_NO_SPACE,
                    tick=40,
                    summary="context_final_failed",
                )
                event.update(mutation)
                self.rejected(lambda v=value, e=event: v.record_event(e))
                snapshot = value.snapshot()
                self.assertEqual(snapshot.termination_cause, "")
                self.assertFalse(snapshot.session_blocked)
                self.assertEqual(snapshot.tasks[0].terminal_event, "")

        value = self.make()
        self.root_prelude(value)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=ledger.AGENT_STATUS_NO_SPACE,
                    tick=40,
                    summary="context_final_failed",
                )
            )
        )
        self.rejected(
            lambda: value.begin_termination(CORR1, "context_final_failed")
        )

    def test_pre_request_and_advanced_user_cancel_are_bounded(self) -> None:
        value = self.make()
        value.record_event(self.event(summary="user_goal_received"))
        value.begin_cancel(CORR1)
        value.record_event(self.event(kind="accepted", tick=11))
        value.record_event(
            self.event(kind="progress", tick=12, metric_code=1, metric_value=8)
        )
        self.rejected(
            lambda: value.record_event(
                self.event(kind="cancelled", status=-10, tick=19)
            )
        )
        value.record_model_request(CORR1)
        value.record_event(
            self.event(kind="cancelled", status=-10, tick=20)
        )
        self.assertTrue(value.assert_turn_complete("cancelled").sealed)

        value = self.make()
        self.root_prelude(value)
        self.child_success(value)
        value.begin_cancel(CORR1)
        value.record_model_request(CORR2)
        self.rejected(lambda: value.record_model_request(CORR3))
        value.record_event(
            self.event(corr_id=CORR2, kind="cancelled", status=-10, tick=50)
        )
        self.assertTrue(value.assert_turn_complete("cancelled").sealed)

    def test_tool_specific_arguments_are_exact_and_body_free(self) -> None:
        malformed = (
            ("search_files", '{"query":"x","extra":1}'),
            ("search_files", '{ "query":"x"}'),
            ("read_file", '{"max_lines":1,"path":"","start_line":1}'),
            ("read_file", '{"max_lines":1,"path":"a","start_line":0}'),
            ("read_file", '{"max_lines":65,"path":"a","start_line":1}'),
            ("inspect_system", '{"operation":"host"}'),
            ("search_files", '{"query":"x","query":"y"}'),
        )
        for tool, arguments in malformed:
            with self.subTest(tool=tool, arguments=arguments):
                value = self.make()
                self.root_prelude(value)
                self.rejected(
                    lambda v=value, t=tool, a=arguments: v.record_delivered_tool(
                        CORR1, t, arguments_canonical=a
                    )
                )

    def test_tool_string_bounds_use_codepoints_and_separate_transport_bytes(self) -> None:
        for tool, arguments in (
            ("search_files", {"query": "界" * 95, "path_prefix": "😀" * 111}),
            ("search_files", {"query": '"' * 95}),
            (
                "read_file",
                {"path": "😀" * 255, "start_line": 0xFFFFFFFF, "max_lines": 64},
            ),
        ):
            with self.subTest(tool=tool):
                self.assertIsInstance(
                    ledger._tool_argument_binding(tool, arguments), str
                )
        for tool, arguments in (
            ("search_files", {"query": "界" * 96}),
            ("search_files", {"query": "x", "path_prefix": "😀" * 112}),
            ("search_files", {"query": "bad\0query"}),
            (
                "read_file",
                {"path": "😀" * 256, "start_line": 1, "max_lines": 1},
            ),
            (
                "read_file",
                {"path": "x", "start_line": 0x100000000, "max_lines": 1},
            ),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                with self.assertRaises(ledger.NexusTaskLedgerError):
                    ledger._tool_argument_binding(tool, arguments)
        for value in (
            "x" * (ledger.TOOL_ARGUMENT_STRING_BYTES + 1),
            "\x01" * 600,
        ):
            with self.assertRaises(ledger.NexusTaskLedgerError):
                ledger._argument_text(
                    value, "transport boundary", maximum=4000, empty=True
                )

    def test_exact_success_provenance_for_every_task_tool(self) -> None:
        for index, tool in enumerate(TOOL_SPEC):
            with self.subTest(tool=tool):
                value = self.make()
                self.root_prelude(value)
                arguments, identity, expected, artifact_expected = TOOL_SPEC[tool]
                value.record_delivered_tool(CORR1, tool, arguments_canonical=arguments)
                if tool in ("search_files", "read_file"):
                    self.workspace_content(
                        value, task_id=1000 + index, tool=tool
                    )
                common = dict(
                    task_id=1000 + index,
                    parent_task_id=ROOT,
                    corr_id=CORR1,
                    identity=identity,
                    deadline_tick=5000,
                )
                value.record_event(self.event(**common, tick=20))
                value.record_event(self.event(**common, kind="completed", tick=22))
                self.rejected(
                    lambda v=value, c=common: v.record_event(
                        self.event(
                            **c,
                            kind="artifact_published",
                            tick=23,
                            digest=SHA_A,
                            provenance=1,
                            resource_used=64,
                        )
                    )
                )
                # Confirm the expected constant itself is not a loose nonzero mask.
                self.assertIn(expected, (53, 60))
                self.assertEqual(artifact_expected, expected)

    def test_workspace_content_requires_exact_settlement_binding(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.child_success(value, tool="read_file")
        self.assertTrue(self.final(value, CORR2).sealed)

        for mutation in (
            {"value0": 1},
            {"value1": 1001},
            {"value2": SYSTEM_ID[2]},
            {"provenance": 53},
            {"projection_sha256": SHA_C},
            {"workspace_source_sha256": SHA_C},
            {"context_seq": 1023},
        ):
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                digest, _handle, resource = self.child_success(
                    value, tool="read_file", settle=False
                )
                data = {
                    "status": 0,
                    "value0": resource,
                    "value1": 1000,
                    "value2": RESEARCH_ID[2],
                    "provenance": 60,
                    "projection_sha256": digest,
                    "context_seq": 1024,
                    "workspace_source_sha256": value.workspace_source_sha256(
                        CORR1
                    ),
                    "result_sha256": SHA_B,
                }
                data.update(mutation)
                self.rejected(
                    lambda v=value, d=data: v.settle_tool(
                        CORR1, tool="read_file", **d
                    )
                )

    def test_coordinator_context_routes_and_consumption_sequence_are_exact(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "read_file")
        self.workspace_content(value, tool="read_file")
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="completed",
                    tick=21,
                    source_pid=RESEARCH_ID[1],
                    target_pid=ROOT_ID[1],
                )
            )
        )
        value.record_event(self.event(**common, kind="completed", tick=21))
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="artifact_published",
                    tick=22,
                    source_pid=RESEARCH_ID[1],
                    target_pid=ROOT_ID[1],
                    digest=SHA_A,
                    provenance=60,
                    resource_used=64,
                )
            )
        )
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="artifact_published",
                    tick=22,
                    context_seq=1021,
                    digest=SHA_A,
                    provenance=60,
                    resource_used=64,
                )
            )
        )
        value.record_event(
            self.event(
                **common,
                kind="artifact_published",
                tick=22,
                digest=SHA_A,
                provenance=60,
                resource_used=64,
            )
        )
        self.rejected(
            lambda: value.settle_tool(
                CORR1,
                tool="read_file",
                status=0,
                value0=64,
                value1=1000,
                value2=RESEARCH_ID[2],
                provenance=60,
                projection_sha256=SHA_A,
                workspace_source_sha256=value.workspace_source_sha256(CORR1),
                context_seq=1021,
                result_sha256=SHA_B,
            )
        )

    def test_workspace_pages_stale_reset_and_source_root_are_exact(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        generation_a = "1" * 64
        generation_b = "2" * 64
        arguments = "3" * 64
        objects = "4" * 64
        empty = hashlib.sha256(b"").hexdigest()

        def exchange(
            operation: str,
            attempt: int,
            request_generation: str,
            result_generation: str,
            status: str,
            content_digest: str,
            content_bytes: int,
            *,
            cursor: int = 0,
            next_cursor: int = 0,
            eof: bool = False,
        ) -> None:
            value.record_workspace_request(
                CORR1,
                task_id=1000,
                tool="search_files",
                operation=operation,
                attempt=attempt,
                workspace_generation=request_generation,
                arguments_sha256=arguments,
                objects_sha256=objects,
                manifest_cursor=cursor if operation == "manifest" else 0,
            )
            value.record_workspace_result(
                CORR1,
                task_id=1000,
                tool="search_files",
                operation=operation,
                attempt=attempt,
                workspace_generation=result_generation,
                arguments_sha256=arguments,
                result_objects_sha256=objects,
                status=status,
                content_bytes=content_bytes,
                content_sha256=content_digest,
                manifest_cursor=cursor if operation == "manifest" else 0,
                manifest_next_cursor=(
                    next_cursor if operation == "manifest" and status == "ok" else 0
                ),
                manifest_eof=eof if operation == "manifest" and status == "ok" else False,
            )

        exchange(
            "manifest", 1, "", generation_a, "ok", SHA_A, 64,
            cursor=0, next_cursor=1,
        )
        exchange("search", 2, generation_a, generation_b, "stale", empty, 0)
        exchange(
            "manifest", 3, "", generation_b, "ok", SHA_B, 32,
            cursor=0, next_cursor=1,
        )
        # A zero-candidate manifest page legitimately has no search exchange.
        exchange(
            "manifest", 4, generation_b, generation_b, "ok", SHA_C, 16,
            cursor=1, next_cursor=1, eof=True,
        )

        source = (
            "workspace_source_attempt_v1\n"
            "operation=manifest\n"
            "attempt=3\n"
            f"request_generation={'0' * 64}\n"
            f"result_generation={generation_b}\n"
            f"arguments_sha256={arguments}\n"
            f"objects_sha256={objects}\n"
            "status=ok\n"
            "content_bytes=32\n"
            f"content_sha256={SHA_B}\n"
            "workspace_source_attempt_v1\n"
            "operation=manifest\n"
            "attempt=4\n"
            f"request_generation={generation_b}\n"
            f"result_generation={generation_b}\n"
            f"arguments_sha256={arguments}\n"
            f"objects_sha256={objects}\n"
            "status=ok\n"
            "content_bytes=16\n"
            f"content_sha256={SHA_C}\n"
        )
        self.assertEqual(
            value.workspace_source_sha256(CORR1),
            hashlib.sha256(source.encode("ascii")).hexdigest(),
        )

        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=30))
        value.record_event(self.event(**common, kind="completed", tick=31))
        value.record_event(
            self.event(
                **common,
                kind="artifact_published",
                tick=32,
                provenance=60,
                resource_used=20,
                digest=SHA_A,
            )
        )
        value.settle_tool(
            CORR1,
            tool="search_files",
            status=0,
            value0=20,
            value1=1000,
            value2=RESEARCH_ID[2],
            provenance=60,
            projection_sha256=SHA_A,
            context_seq=1032,
            workspace_source_sha256=value.workspace_source_sha256(CORR1),
            result_sha256=SHA_B,
        )

    def test_workspace_attempt_order_binding_and_cancellation_fail_closed(self) -> None:
        generation = "1" * 64
        arguments = "2" * 64
        objects = "3" * 64

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        request = dict(
            task_id=1000,
            tool="search_files",
            operation="manifest",
            attempt=1,
            workspace_generation="",
            arguments_sha256=arguments,
            objects_sha256=objects,
            manifest_cursor=0,
        )
        value.record_workspace_request(CORR1, **request)
        self.rejected(lambda: value.record_workspace_request(CORR1, **request))
        self.rejected(
            lambda: value.record_workspace_result(
                CORR2,
                **{
                    key: item
                    for key, item in request.items()
                    if key not in ("objects_sha256", "workspace_generation")
                },
                workspace_generation=generation,
                result_objects_sha256=objects,
                status="ok",
                content_bytes=1,
                content_sha256=SHA_A,
                manifest_next_cursor=1,
                manifest_eof=False,
            )
        )
        value.begin_cancel(CORR1)
        self.rejected(
            lambda: value.record_workspace_result(
                CORR1,
                **{
                    key: item
                    for key, item in request.items()
                    if key not in ("objects_sha256", "workspace_generation")
                },
                workspace_generation=generation,
                result_objects_sha256=objects,
                status="ok",
                content_bytes=1,
                content_sha256=SHA_A,
                manifest_next_cursor=1,
                manifest_eof=False,
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.rejected(
            lambda: value.record_workspace_request(
                CORR1,
                **{**request, "workspace_generation": generation},
            )
        )
        value.record_workspace_request(CORR1, **request)
        value.record_workspace_result(
            CORR1,
            **{
                key: item
                for key, item in request.items()
                if key not in ("objects_sha256", "workspace_generation")
            },
            workspace_generation=generation,
            result_objects_sha256=objects,
            status="ok",
            content_bytes=1,
            content_sha256=SHA_A,
            manifest_next_cursor=1,
            manifest_eof=False,
        )
        next_manifest = {
            **request,
            "attempt": 2,
            "workspace_generation": generation,
            "arguments_sha256": "4" * 64,
            "manifest_cursor": 1,
        }
        self.rejected(
            lambda: value.record_workspace_request(
                CORR1,
                **{**next_manifest, "workspace_generation": ""},
            )
        )
        value.record_workspace_request(CORR1, **next_manifest)
        self.rejected(
            lambda: value.record_workspace_result(
                CORR1,
                **{
                    key: item
                    for key, item in next_manifest.items()
                    if key not in ("objects_sha256", "workspace_generation")
                },
                workspace_generation="5" * 64,
                result_objects_sha256=objects,
                status="ok",
                content_bytes=1,
                content_sha256=SHA_A,
                manifest_next_cursor=2,
                manifest_eof=False,
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        value.begin_cancel(CORR1)
        self.rejected(
            lambda: value.record_workspace_request(CORR1, **request)
        )

    def test_new_workspace_correlation_starts_fresh_then_reuses_manifest(self) -> None:
        generation = "d" * 64
        objects = "e" * 64
        manifest_arguments = hashlib.sha256(
            b'{"cursor":0,"limit":8}'
        ).hexdigest()
        read_arguments = hashlib.sha256(
            TOOL_SPEC["read_file"][0].encode("utf-8")
        ).hexdigest()
        value = self.make()
        self.root_prelude(value)
        self.child_success(value)
        value.record_model_request(CORR2)
        self.deliver(value, CORR2, "read_file")

        first_manifest = dict(
            task_id=1001,
            tool="read_file",
            operation="manifest",
            attempt=1,
            workspace_generation="",
            arguments_sha256=manifest_arguments,
            objects_sha256=objects,
            manifest_cursor=0,
        )
        self.rejected(
            lambda: value.record_workspace_request(
                CORR2,
                **{**first_manifest, "workspace_generation": generation},
            )
        )
        value.record_workspace_request(CORR2, **first_manifest)
        value.record_workspace_result(
            CORR2,
            task_id=1001,
            tool="read_file",
            operation="manifest",
            attempt=1,
            workspace_generation=generation,
            arguments_sha256=manifest_arguments,
            result_objects_sha256=objects,
            status="ok",
            content_bytes=128,
            content_sha256=SHA_C,
            manifest_cursor=0,
            manifest_next_cursor=1,
            manifest_eof=True,
        )
        value.record_workspace_request(
            CORR2,
            task_id=1001,
            tool="read_file",
            operation="read",
            attempt=2,
            workspace_generation=generation,
            arguments_sha256=read_arguments,
            objects_sha256=objects,
            manifest_cursor=0,
        )
        attempts = value._tools[CORR2].workspace_attempts
        self.assertEqual(attempts[0].request_generation, "")
        self.assertEqual(attempts[0].result_generation, generation)
        self.assertEqual(attempts[1].request_generation, generation)
        self.assertEqual(attempts[1].request_objects_sha256, objects)

    def test_workspace_attempt_bound_covers_full_repository_paging(self) -> None:
        max_objects = 10000
        minimum_bounded_page = 9
        generations_with_two_stale_restarts = 3
        pages = (max_objects + minimum_bounded_page - 1) // minimum_bounded_page
        worst_case = pages * 2 * generations_with_two_stale_restarts
        self.assertEqual(worst_case, 6672)
        self.assertEqual(ledger.MAX_WORKSPACE_ATTEMPTS, 8192)
        self.assertGreaterEqual(ledger.MAX_WORKSPACE_ATTEMPTS, worst_case)

    def test_workspace_manifest_cursor_chain_and_eof_are_exact(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        generation = "1" * 64

        def request(attempt: int, cursor: int, request_generation: str) -> None:
            value.record_workspace_request(
                CORR1,
                task_id=1000,
                tool="search_files",
                operation="manifest",
                attempt=attempt,
                workspace_generation=request_generation,
                arguments_sha256=f"{attempt:064x}",
                objects_sha256=SHA_A,
                manifest_cursor=cursor,
            )

        def result(
            attempt: int, cursor: int, next_cursor: int, *, eof: bool
        ) -> None:
            value.record_workspace_result(
                CORR1,
                task_id=1000,
                tool="search_files",
                operation="manifest",
                attempt=attempt,
                workspace_generation=generation,
                arguments_sha256=f"{attempt:064x}",
                result_objects_sha256=SHA_B,
                status="ok",
                content_bytes=1,
                content_sha256=SHA_C,
                manifest_cursor=cursor,
                manifest_next_cursor=next_cursor,
                manifest_eof=eof,
            )

        request(1, 0, "")
        result(1, 0, 32, eof=False)
        self.rejected(lambda: request(2, 0, generation))
        self.rejected(lambda: request(2, 64, generation))
        request(2, 32, generation)
        result(2, 32, 40, eof=True)
        self.rejected(lambda: request(3, 40, generation))

    def test_routing_is_exact_for_root_worker_and_synthetic_terminal(self) -> None:
        value = self.make()
        self.rejected(
            lambda: value.record_event(self.event(target_pid=RESEARCH_ID[1]))
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        self.rejected(
            lambda: value.record_event(
                self.event(**common, tick=20, source_pid=RESEARCH_ID[1])
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.record_event(self.event(**common, tick=20))
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="failed",
                    status=-20,
                    tick=21,
                    source_pid=RESEARCH_ID[1],
                    target_pid=ROOT_ID[1],
                    summary="worker_not_quiescent;session_blocked=1",
                )
            )
        )

    def test_task_channel_assignment_may_terminalize_from_queued_state(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="failed", status=-2, tick=21)
        )
        value.settle_tool(
            CORR1,
            status=-2,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_B,
        )
        self.assertTrue(self.final(value, CORR2).sealed)

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="completed", tick=21)
        )
        self.assertEqual(value.snapshot().tasks[1].terminal_event, "completed")
        self.rejected(lambda: value.record_model_request(CORR2))

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.begin_cancel(CORR1)
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="cancelled", status=-10, tick=21)
        )
        value.settle_cancelled_tool_from_task(CORR1)
        value.record_event(
            self.event(kind="cancelled", status=-10, tick=30)
        )
        self.assertTrue(value.assert_turn_complete("cancelled").sealed)

        for mutation in (
            {"summary": "research_queued;transport=message"},
            {"summary": "task_channel_v1;phase=assigned;channel_generation=7"},
        ):
            value = self.make()
            self.root_prelude(value)
            self.deliver(value, CORR1, "search_files")
            self.workspace_content(value)
            self.rejected(
                lambda v=value, m=mutation: v.record_event(
                    self.event(**common, tick=20, **m)
                )
            )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.record_event(self.event(**common, tick=20))
        self.rejected(
            lambda: value.record_event(
                self.event(**common, kind="accepted", tick=21)
            )
        )
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="completed",
                    tick=21,
                    context_seq=0,
                )
            )
        )
        self.rejected(
            lambda: value.record_event(
                self.event(
                    **common,
                    kind="completed",
                    tick=21,
                    summary=(
                        "task_channel_v1;phase=cqe;channel_generation=7;"
                        "request_id=99999;slot_generation=1000;tool_id=26;"
                        "contract_generation=9"
                    ),
                )
            )
        )

    def test_indeterminate_latches_session_block_and_forbids_success(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(
                **common,
                kind="failed",
                status=-20,
                tick=21,
                summary="worker_not_quiescent;session_blocked=1",
            )
        )
        self.assertTrue(value.snapshot().session_blocked)
        self.rejected(lambda: value.record_model_request(CORR2))
        self.rejected(lambda: value.freeze_provider_final(CORR1))
        value.settle_tool(
            CORR1,
            status=-18,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_B,
        )
        value.record_event(
            self.event(kind="failed", status=-18, tick=40)
        )
        self.assertTrue(value.assert_turn_complete("error").session_blocked)
        value.clear()
        self.rejected(lambda: value.begin_turn(TURN + 1, REQUEST + 1))
        value.clear(reset_session=True)
        value.begin_turn(TURN + 1, REQUEST + 1)

    def test_no_child_cleanup_block_stages_root_before_exact_tool_result(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        roots: list[tuple[str, str]] = []
        for _ in range(2):
            value = self.make()
            self.root_prelude(value)
            self.deliver(value, CORR1, "search_files")

            # nexus_execute_open_decision publishes the root failure through
            # the result pipe before the enclosing TOOL_EVENT is serialized.
            value.record_event(
                self.event(
                    kind="failed", status=-18, tick=40, summary=marker
                )
            )
            staged = value.snapshot()
            self.assertTrue(staged.session_blocked)
            self.assertEqual(staged.termination_cause, "session_error")
            self.assertFalse(staged.all_required_terminal)

            value.settle_tool(
                CORR1,
                tool="search_files",
                status=-18,
                provenance=ledger.NEXUS_PROVENANCE_FAILURE,
                result_sha256=SHA_B,
                session_blocked_marker=marker,
            )
            sealed = value.assert_turn_complete("error")
            self.assertTrue(sealed.session_blocked)
            self.assertNotIn(marker, repr(value.__dict__))
            roots.append(
                (sealed.task_root_sha256, sealed.artifact_root_sha256)
            )
            value.clear()
            self.rejected(lambda v=value: v.begin_turn(TURN + 1, REQUEST + 1))
        self.assertEqual(roots[0], roots[1])

    def test_completed_child_cleanup_block_preserves_task_and_artifact_proof(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        roots: list[tuple[str, str]] = []
        for _ in range(2):
            value = self.make()
            self.root_prelude(value)
            digest, _handle, _resource = self.child_success(value, settle=False)

            value.record_event(
                self.event(
                    kind="failed", status=-18, tick=40, summary=marker
                )
            )
            staged = value.snapshot()
            self.assertTrue(staged.session_blocked)
            self.assertEqual(staged.termination_cause, "session_error")
            self.assertEqual(staged.tasks[1].terminal_event, "completed")
            self.assertEqual(staged.tasks[1].artifact_sha256, digest)

            value.settle_tool(
                CORR1,
                tool="search_files",
                status=-18,
                provenance=ledger.NEXUS_PROVENANCE_FAILURE,
                result_sha256=SHA_B,
                session_blocked_marker=marker,
            )
            sealed = value.assert_turn_complete("error")
            self.assertTrue(value._tools[CORR1].session_blocked)
            self.assertEqual(sealed.tasks[1].terminal_event, "completed")
            self.assertEqual(sealed.tasks[1].artifact_sha256, digest)
            roots.append(
                (sealed.task_root_sha256, sealed.artifact_root_sha256)
            )
            value.clear()
            self.rejected(lambda v=value: v.begin_turn(TURN + 1, REQUEST + 1))
        self.assertEqual(roots[0], roots[1])

    def test_completed_child_cleanup_block_rejects_incomplete_child(self) -> None:
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.record_event(self.event(**common, tick=20))
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-18,
                    tick=40,
                    summary="artifact_cleanup_failed;session_blocked=1",
                )
            )
        )

    def test_completed_child_cleanup_block_requires_failure_and_exact_marker(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        value = self.make()
        self.root_prelude(value)
        digest, handle, _resource = self.child_success(value, settle=False)
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        self.rejected(
            lambda: value.settle_tool(
                CORR1,
                tool="search_files",
                status=0,
                value0=handle,
                value1=1000,
                value2=RESEARCH_ID[2],
                provenance=TOOL_SPEC["search_files"][2],
                projection_sha256=digest,
                result_sha256=SHA_B,
                session_blocked_marker=marker,
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.child_success(value, settle=False)
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        self.rejected(
            lambda: value.settle_tool(
                CORR1,
                tool="search_files",
                status=-18,
                provenance=ledger.NEXUS_PROVENANCE_FAILURE,
                result_sha256=SHA_B,
                session_blocked_marker="cancel_not_quiescent;session_blocked=1",
            )
        )

    def test_failed_child_cleanup_block_accepts_exact_overriding_tool_failure(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="failed", status=-2, tick=21)
        )
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        value.settle_tool(
            CORR1,
            tool="search_files",
            status=-18,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_B,
            session_blocked_marker=marker,
        )
        snapshot = value.assert_turn_complete("error")
        child = next(task for task in snapshot.tasks if task.parent_task_id)
        self.assertEqual((child.terminal_event, child.terminal_status), ("failed", -2))
        self.assertTrue(value._tools[CORR1].session_blocked)

    def test_timeout_cancelled_child_cleanup_block_accepts_exact_override(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="cancelled", status=-10, tick=21)
        )
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        value.settle_tool(
            CORR1,
            tool="search_files",
            status=-18,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_B,
            session_blocked_marker=marker,
        )
        snapshot = value.assert_turn_complete("error")
        child = next(task for task in snapshot.tasks if task.parent_task_id)
        self.assertEqual(
            (child.terminal_event, child.terminal_status), ("cancelled", -10)
        )

    def test_active_cancel_cleanup_block_defers_synthetic_settlement_to_turn_complete(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.begin_cancel(CORR1)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="cancelled", status=-10, tick=21)
        )
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        self.assertEqual(value.snapshot().settled_tool_count, 0)
        self.rejected(
            lambda: value.settle_cancelled_cleanup_tool_at_turn_complete(
                CORR1, turn_status="cancelled"
            )
        )
        self.assertTrue(
            value.settle_cancelled_cleanup_tool_at_turn_complete(
                CORR1, turn_status="error"
            )
        )
        self.assertFalse(
            value.settle_cancelled_cleanup_tool_at_turn_complete(
                CORR1, turn_status="error"
            )
        )
        snapshot = value.assert_turn_complete("error")
        self.assertTrue(snapshot.session_blocked)
        self.assertTrue(value._tools[CORR1].session_blocked)

    def test_active_cancel_cleanup_block_accepts_real_tool_after_root(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "search_files")
        self.workspace_content(value)
        value.begin_cancel(CORR1)
        common = dict(
            task_id=1000,
            parent_task_id=ROOT,
            corr_id=CORR1,
            identity=RESEARCH_ID,
            deadline_tick=5000,
        )
        value.record_event(self.event(**common, tick=20))
        value.record_event(
            self.event(**common, kind="cancelled", status=-10, tick=21)
        )
        value.record_event(
            self.event(kind="failed", status=-18, tick=40, summary=marker)
        )
        self.assertEqual(value.snapshot().settled_tool_count, 0)
        value.settle_tool(
            CORR1,
            tool="search_files",
            status=-18,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_B,
            session_blocked_marker=marker,
        )
        self.assertFalse(
            value.settle_cancelled_cleanup_tool_at_turn_complete(
                CORR1, turn_status="error"
            )
        )
        self.assertTrue(value.assert_turn_complete("error").session_blocked)

    def test_terminal_cleanup_failure_upgrades_owned_outcomes_to_session_error(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        def cleanup_root(value: ledger.NexusTaskLedger, corr: int) -> None:
            value.record_event(
                self.event(
                    corr_id=corr,
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary=marker,
                )
            )
            snapshot = value.assert_turn_complete("error")
            self.assertTrue(snapshot.session_blocked)
            self.assertEqual(snapshot.termination_cause, "session_error")
            value.clear()
            self.rejected(
                lambda v=value: v.begin_turn(TURN + 1, REQUEST + 1)
            )

        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        cleanup_root(value, CORR1)

        value = self.make()
        self.root_prelude(value)
        value.record_model_error(CORR1, retryable=False)
        value.begin_termination(CORR1, "provider_fatal")
        cleanup_root(value, CORR1)

        value = self.make()
        self.root_prelude(value)
        value.begin_cancel(CORR1)
        cleanup_root(value, CORR1)

        value = self.make()
        self.root_prelude(value)
        value.record_model_error(CORR1, retryable=True)
        value.begin_termination(CORR1, "round_limit")
        cleanup_root(value, CORR1)

        value = self.make()
        self.root_prelude(value)
        self.child_success(value)
        value.begin_cancel(CORR1)
        cleanup_root(value, CORR1)

        value = self.make()
        self.root_prelude(value)
        self.child_success(value)
        value.begin_termination(CORR1, "round_limit")
        cleanup_root(value, CORR1)

    def test_terminal_cleanup_failure_rejects_wrong_marker_corr_and_unowned_outcome(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary="turn_failed",
                )
            )
        )

        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        value.record_event(
            self.event(kind="completed", status=0, tick=79)
        )
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary=marker,
                )
            )
        )

        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    corr_id=CORR2,
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary=marker,
                )
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary=marker,
                )
            )
        )

        value = self.make()
        self.root_prelude(value)
        value.record_model_error(CORR1, retryable=True)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-18,
                    tick=80,
                    summary=marker,
                )
            )
        )

        value = self.make()
        self.root_prelude(value)
        value.freeze_provider_final(CORR1)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    kind="failed",
                    status=-2,
                    tick=80,
                    summary=marker,
                )
            )
        )

    def test_no_child_cleanup_block_requires_exact_negative_settlement(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        mutations = (
            {"session_blocked_marker": ""},
            {"session_blocked_marker": "cancel_not_quiescent;session_blocked=1"},
            {"status": -2},
            {"value0": 1},
            {"provenance": 1},
            {"projection_sha256": SHA_A},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                self.deliver(value, CORR1, "search_files")
                value.record_event(
                    self.event(
                        kind="failed", status=-18, tick=40, summary=marker
                    )
                )
                settlement = {
                    "status": -18,
                    "provenance": ledger.NEXUS_PROVENANCE_FAILURE,
                    "result_sha256": SHA_B,
                    "session_blocked_marker": marker,
                }
                settlement.update(mutation)
                self.rejected(
                    lambda v=value, data=settlement: v.settle_tool(
                        CORR1, tool="search_files", **data
                    )
                )

    def test_unknown_corr_duplicate_and_identity_mutations_reject(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.rejected(
            lambda: value.settle_tool(
                CORR2, status=-2, provenance=0, result_sha256=SHA_B
            )
        )

        value = self.make(identities=False)
        self.root_prelude(value)
        self.assertFalse(value.snapshot().tasks[0].identity_verified)
        self.rejected(
            lambda: value.set_kernel_identity(
                role="coordinator", pid=ROOT_ID[1], agent_id=999, control_id=4000
            )
        )

    def test_control_identity_uses_full_u64_without_widening_other_ids(self) -> None:
        for control_id in (1 << 63, (1 << 64) - 1):
            with self.subTest(control_id=control_id):
                identity = ("coordinator", ROOT_ID[1], ROOT_ID[2], control_id)
                value = self.make(identities=False)
                value.set_kernel_identity(
                    role=identity[0],
                    pid=identity[1],
                    agent_id=identity[2],
                    control_id=identity[3],
                )
                value.record_event(self.event(identity=identity))
                self.assertTrue(value.snapshot().tasks[0].identity_verified)
                self.assertEqual(value.snapshot().tasks[0].control_id, control_id)

        for control_id in (0, 1 << 64):
            with self.subTest(rejected_control_id=control_id):
                value = self.make(identities=False)
                self.rejected(
                    lambda: value.set_kernel_identity(
                        role="coordinator",
                        pid=ROOT_ID[1],
                        agent_id=ROOT_ID[2],
                        control_id=control_id,
                    )
                )

        value = self.make(identities=False)
        self.rejected(lambda: value.begin_turn(1 << 63, REQUEST))
        self.rejected(lambda: value.record_model_request(1 << 63))


if __name__ == "__main__":
    unittest.main()
