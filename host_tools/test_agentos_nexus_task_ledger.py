#!/usr/bin/env python3
"""Mutation tests built from the real Nexus Guest TASK/TOOL ordering."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import re
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
ANALYST_ID = ("analyst", 43, 403, 4003)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
REPORT_HANDLE = (GENERATION << 16) | 9

TOOL_SPEC = {
    "source_search": ('{"query":"nexus_root_start","path_prefix":"user/"}', RESEARCH_ID, 60),
    "source_read": ('{"source_id":"S0001","start_line":40,"max_lines":8}', RESEARCH_ID, 60),
    "inspect_runtime": ('{"operation":"system_status"}', SYSTEM_ID, 53),
    "draft_report": ('{"content":"Exact model report.","title":"Result"}', ANALYST_ID, 52),
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
                ANALYST_ID,
            ):
                value.set_kernel_identity(
                    role=role,
                    pid=pid,
                    agent_id=agent_id,
                    control_id=control_id,
                )
        return value

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
            elif (
                kind == "failed"
                and status == ledger.AGENT_STATUS_TASK_FAILED
                and optional.get("summary")
                == "worker_not_quiescent;session_blocked=1"
            ):
                route = (ROOT_ID[1], pid)
            elif kind == "assigned":
                route = (ROOT_ID[1], pid)
            else:
                route = (pid, ROOT_ID[1])
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
        arguments, _identity, _provenance = TOOL_SPEC[tool]
        value.record_delivered_tool(corr, tool, arguments_canonical=arguments)
        return arguments

    def child_success(
        self,
        value: ledger.NexusTaskLedger,
        *,
        corr: int = CORR1,
        task_id: int = 1000,
        tool: str = "source_search",
        digest: str | None = None,
        settle: bool = True,
    ) -> tuple[str, int, int]:
        arguments = self.deliver(value, corr, tool)
        _args, identity, provenance = TOOL_SPEC[tool]
        content = ""
        handle = 0
        resource = 64
        if tool == "draft_report":
            content = "Exact model report."
            digest = hashlib.sha256(content.encode()).hexdigest()
            handle = REPORT_HANDLE
            resource = len(content.encode())
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
        value.record_event(self.event(**common, kind="accepted", tick=21))
        value.record_event(
            self.event(**common, kind="progress", tick=22, context_seq=7)
        )
        value.record_event(self.event(**common, kind="completed", tick=23))
        value.record_event(
            self.event(
                **common,
                kind="artifact_published",
                tick=24,
                artifact_handle=handle,
                provenance=provenance,
                resource_used=resource,
                digest=digest,
                summary="model_report_preserved"
                if tool == "draft_report"
                else "source_evidence_ready",
            )
        )
        if settle:
            evidence: dict[str, object] = {}
            if tool == "source_read":
                evidence = {
                    "evidence_task_id": task_id,
                    "evidence_provenance": provenance,
                    "evidence_artifact_sha256": digest,
                    "evidence_projection_sha256": digest,
                }
            value.settle_tool(
                corr,
                tool=tool,
                status=0,
                value0=handle,
                value1=task_id,
                value2=identity[2],
                provenance=provenance,
                projection_sha256=digest,
                result_sha256=SHA_B,
                **evidence,
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

    def test_real_source_trace_seals_deterministically_without_bodies(self) -> None:
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
                "source_search",
                arguments_canonical=TOOL_SPEC["source_search"][0],
            )
        )
        self.rejected(lambda: value.record_model_request(CORR2))
        self.deliver(value, CORR1, "source_search")
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

    def test_round_limit_direct_read_root_may_precede_tool_event(self) -> None:
        value = self.make()
        self.root_prelude(value)
        digest, handle, resource = self.child_success(
            value, tool="draft_report"
        )
        value.record_model_request(CORR2)
        value.record_delivered_tool(
            CORR2,
            "read_artifact",
            arguments_canonical=f'{{"handle":{handle}}}',
        )
        value.begin_termination(CORR2, "round_limit")
        value.record_event(
            self.event(corr_id=CORR2, kind="cancelled", status=-10, tick=80)
        )
        self.assertFalse(value.all_required_terminal)
        value.settle_tool(
            CORR2,
            tool="read_artifact",
            status=0,
            value0=handle,
            value1=resource,
            value2=ledger.AGENT_NEXUS_ARTIFACT_REPORT,
            provenance=52,
            projection_sha256=digest,
            result_sha256=SHA_C,
        )
        snapshot = value.assert_turn_complete("cancelled")
        self.assertTrue(snapshot.all_required_terminal)

    def test_direct_read_user_cancel_settles_before_root_and_round_limit_wins(self) -> None:
        value = self.make()
        self.root_prelude(value)
        digest, handle, resource = self.child_success(
            value, tool="draft_report"
        )
        value.record_model_request(CORR2)
        value.record_delivered_tool(
            CORR2,
            "read_artifact",
            arguments_canonical=f'{{"handle":{handle}}}',
        )
        value.begin_cancel(CORR2)
        self.rejected(
            lambda: value.record_event(
                self.event(
                    corr_id=CORR2, kind="cancelled", status=-10, tick=80
                )
            )
        )
        value.settle_tool(
            CORR2,
            tool="read_artifact",
            status=0,
            value0=handle,
            value1=resource,
            value2=ledger.AGENT_NEXUS_ARTIFACT_REPORT,
            provenance=52,
            projection_sha256=digest,
            result_sha256=SHA_C,
        )
        value.record_event(
            self.event(corr_id=CORR2, kind="cancelled", status=-10, tick=81)
        )
        self.assertTrue(value.assert_turn_complete("cancelled").sealed)

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "source_search")
        value.begin_termination(CORR1, "round_limit")
        self.rejected(lambda: value.begin_cancel(CORR1))
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
        value.settle_cancelled_tool_from_task(CORR1)
        value.record_event(
            self.event(corr_id=CORR1, kind="cancelled", status=-10, tick=30)
        )
        self.assertTrue(value.assert_turn_complete("cancelled").sealed)

    def test_stale_read_is_recorded_as_negative_direct_proof(self) -> None:
        baseline = self.make()
        self.root_prelude(baseline)
        self.child_success(baseline, tool="draft_report")
        baseline_root = self.final(baseline, CORR2).task_root_sha256

        value = self.make()
        self.root_prelude(value)
        self.child_success(value, tool="draft_report")
        value.record_model_request(CORR2)
        value.record_delivered_tool(
            CORR2,
            "read_artifact",
            arguments_canonical=f'{{"handle":{REPORT_HANDLE + 1}}}',
        )
        value.settle_tool(
            CORR2,
            tool="read_artifact",
            status=-4,
            provenance=ledger.NEXUS_PROVENANCE_FAILURE,
            result_sha256=SHA_C,
        )
        snapshot = self.final(value, CORR3)
        self.assertNotEqual(baseline_root, snapshot.task_root_sha256)

    def test_read_success_binds_latest_draft_handle_content_and_size(self) -> None:
        mutations = (
            {"value0": REPORT_HANDLE + 1},
            {"value1": 1},
            {"value2": 6},
            {"provenance": 1},
            {"projection_sha256": SHA_A},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                digest, handle, resource = self.child_success(
                    value, tool="draft_report"
                )
                value.record_model_request(CORR2)
                value.record_delivered_tool(
                    CORR2,
                    "read_artifact",
                    arguments_canonical=f'{{"handle":{handle}}}',
                )
                settlement = {
                    "status": 0,
                    "value0": handle,
                    "value1": resource,
                    "value2": ledger.AGENT_NEXUS_ARTIFACT_REPORT,
                    "provenance": 52,
                    "projection_sha256": digest,
                    "result_sha256": SHA_C,
                }
                settlement.update(mutation)
                self.rejected(
                    lambda v=value, data=settlement: v.settle_tool(
                        CORR2, tool="read_artifact", **data
                    )
                )

    def test_direct_read_changes_both_proof_roots(self) -> None:
        baseline = self.make()
        self.root_prelude(baseline)
        self.child_success(baseline, tool="draft_report")
        first = self.final(baseline, CORR2)

        value = self.make()
        self.root_prelude(value)
        digest, handle, resource = self.child_success(
            value, tool="draft_report"
        )
        value.record_model_request(CORR2)
        value.record_delivered_tool(
            CORR2, "read_artifact", arguments_canonical=f'{{"handle":{handle}}}'
        )
        value.settle_tool(
            CORR2,
            tool="read_artifact",
            status=0,
            value0=handle,
            value1=resource,
            value2=7,
            provenance=52,
            projection_sha256=digest,
            result_sha256=SHA_C,
        )
        second = self.final(value, CORR3)
        self.assertNotEqual(first.task_root_sha256, second.task_root_sha256)
        self.assertNotEqual(first.artifact_root_sha256, second.artifact_root_sha256)

    def test_tool_specific_arguments_are_exact_and_body_free(self) -> None:
        malformed = (
            ("source_search", '{"query":"x","extra":1}'),
            ("source_search", '{ "query":"x"}'),
            ("source_read", '{"source_id":"bad","start_line":1,"max_lines":1}'),
            ("source_read", '{"source_id":"S0001","start_line":0,"max_lines":1}'),
            ("inspect_runtime", '{"operation":"host"}'),
            ("draft_report", '{"content":""}'),
            ("read_artifact", '{"handle":0}'),
            ("source_search", '{"query":"x","query":"y"}'),
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

    def test_draft_binds_exact_content_and_generation_handle(self) -> None:
        for mutation in (
            {"digest": SHA_A},
            {"resource_used": 1},
            {"artifact_handle": 5},
            {"artifact_handle": (2 << 16) | 9},
            {"provenance": 1},
        ):
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                self.deliver(value, CORR1, "draft_report")
                common = dict(
                    task_id=1000,
                    parent_task_id=ROOT,
                    corr_id=CORR1,
                    identity=ANALYST_ID,
                    deadline_tick=5000,
                )
                value.record_event(self.event(**common, tick=20))
                value.record_event(self.event(**common, kind="accepted", tick=21))
                value.record_event(self.event(**common, kind="completed", tick=22))
                artifact = {
                    "digest": hashlib.sha256(b"Exact model report.").hexdigest(),
                    "resource_used": len(b"Exact model report."),
                    "artifact_handle": REPORT_HANDLE,
                    "provenance": 52,
                }
                artifact.update(mutation)
                self.rejected(
                    lambda v=value, c=common, a=artifact: v.record_event(
                        self.event(
                            **c, kind="artifact_published", tick=23, **a
                        )
                    )
                )

    def test_exact_success_provenance_for_every_task_tool(self) -> None:
        for index, tool in enumerate(TOOL_SPEC):
            with self.subTest(tool=tool):
                value = self.make()
                self.root_prelude(value)
                if tool == "draft_report":
                    # The draft-specific trace already exercises exact 52.
                    self.child_success(value, tool=tool)
                    continue
                arguments, identity, expected = TOOL_SPEC[tool]
                value.record_delivered_tool(CORR1, tool, arguments_canonical=arguments)
                common = dict(
                    task_id=1000 + index,
                    parent_task_id=ROOT,
                    corr_id=CORR1,
                    identity=identity,
                    deadline_tick=5000,
                )
                value.record_event(self.event(**common, tick=20))
                value.record_event(self.event(**common, kind="accepted", tick=21))
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

    def test_source_read_four_way_digest_and_provenance_binding(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.child_success(value, tool="source_read")
        self.assertTrue(self.final(value, CORR2).sealed)

        for mutation in (
            {"evidence_task_id": 1001},
            {"evidence_provenance": 1},
            {"evidence_artifact_sha256": SHA_C},
            {"evidence_projection_sha256": SHA_C},
        ):
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                self.child_success(value, tool="source_read", settle=False)
                data = {
                    "status": 0,
                    "value0": 0,
                    "value1": 1000,
                    "value2": RESEARCH_ID[2],
                    "provenance": 60,
                    "projection_sha256": SHA_A,
                    "result_sha256": SHA_B,
                    "evidence_task_id": 1000,
                    "evidence_provenance": 60,
                    "evidence_artifact_sha256": SHA_A,
                    "evidence_projection_sha256": SHA_A,
                }
                data.update(mutation)
                self.rejected(
                    lambda v=value, d=data: v.settle_tool(
                        CORR1, tool="source_read", **d
                    )
                )

    def test_routing_is_exact_for_root_worker_and_synthetic_terminal(self) -> None:
        value = self.make()
        self.rejected(
            lambda: value.record_event(self.event(target_pid=RESEARCH_ID[1]))
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "source_search")
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
        self.deliver(value, CORR1, "source_search")
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

    def test_assigned_may_fail_or_cancel_but_completed_needs_acceptance(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "source_search")
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
        self.deliver(value, CORR1, "source_search")
        value.record_event(self.event(**common, tick=20))
        self.rejected(
            lambda: value.record_event(
                self.event(**common, kind="completed", tick=21)
            )
        )

        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "source_search")
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

    def test_indeterminate_latches_session_block_and_forbids_success(self) -> None:
        value = self.make()
        self.root_prelude(value)
        self.deliver(value, CORR1, "source_search")
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
            self.deliver(value, CORR1, "source_search")

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
                tool="source_search",
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
                tool="source_search",
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
        self.deliver(value, CORR1, "source_search")
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
                tool="source_search",
                status=0,
                value0=handle,
                value1=1000,
                value2=RESEARCH_ID[2],
                provenance=TOOL_SPEC["source_search"][2],
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
                tool="source_search",
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
        self.deliver(value, CORR1, "source_search")
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
            tool="source_search",
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
        self.deliver(value, CORR1, "source_search")
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
            tool="source_search",
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
        self.deliver(value, CORR1, "source_search")
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
        self.deliver(value, CORR1, "source_search")
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
            tool="source_search",
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
            {"evidence_task_id": 1000},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = self.make()
                self.root_prelude(value)
                self.deliver(value, CORR1, "source_search")
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
                        CORR1, tool="source_search", **data
                    )
                )

        value = self.make()
        self.root_prelude(value)
        self.child_success(value, tool="draft_report")
        value.record_model_request(CORR2)
        value.record_delivered_tool(
            CORR2,
            "read_artifact",
            arguments_canonical=f'{{"handle":{REPORT_HANDLE}}}',
        )
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
