#!/usr/bin/env python3
"""Mutation tests for the task-agnostic Nexus replay proof."""

from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_contract as contract  # noqa: E402
import agentos_nexus_task_ledger as ledger_module  # noqa: E402
import agentos_source_attestation as source  # noqa: E402
import validate_agentos_nexus_replay as validator  # noqa: E402


SESSION = "0123456789abcdef0123456789abcdef"
LIFECYCLE = 77
LIFECYCLE_GENERATION = 1
GOALS = (
    "Briefly explain what an autonomous engineering agent is; do not inspect the kernel.",
    "Inspect this boot and the bounded source snapshot, recover from a missing search, cite one exact source range, then preserve and re-read your concise report before answering.",
)
IDENTITIES = {
    "coordinator": (10, 100, 1000),
    "system": (11, 101, 1001),
    "research": (12, 102, 1002),
    "analyst": (13, 103, 1003),
}


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SyntheticAttestor:
    def __init__(self) -> None:
        self.corpus_revision = "1" * 64
        self.manifest_sha256 = "2" * 64
        self.source_id = "S0001"
        self.path = "user/lib/autonomy_example.c"
        self.full_content = b"void nexus_root_start(void) {\n    return;\n}\n"
        self.full_sha256 = hashlib.sha256(self.full_content).hexdigest()
        self.read_content = self.full_content
        self.chunk_sha256 = self.full_sha256
        self.citation = "[S0001:L1-L3]"
        self.read_projection = (
            "scope=build_source_snapshot\n"
            "bounded=1\n"
            "allowlist=os/,include/,user/lib/,user/include/\n"
            "content_untrusted=1\n"
            f"citation={self.citation}\n"
            f"source_id={self.source_id}\n"
            f"path={self.path}\n"
            "start_line=1\nend_line=3\n"
            f"revision={self.corpus_revision}\n"
            f"manifest_sha256={self.manifest_sha256}\n"
            f"full_sha256={self.full_sha256}\n"
            f"chunk_sha256={self.chunk_sha256}\n"
            "--- source data ---\n"
            + self.read_content.decode("utf-8")
        )
        self.read_sha256 = _sha_text(self.read_projection)
        match = source.SearchMatch(
            source_id=self.source_id,
            path=self.path,
            line=1,
            citation="[S0001:L1-L1]",
            full_sha256=self.full_sha256,
            chunk_sha256=hashlib.sha256(
                b"void nexus_root_start(void) {\n"
            ).hexdigest(),
            snippet="void nexus_root_start(void) {",
        )
        self.search_projection = (
            "scope=build_source_snapshot\nbounded=1\n"
            "allowlist=os/,include/,user/lib/,user/include/\n"
            "content_untrusted=1\n"
            f"revision={self.corpus_revision}\n"
            f"manifest_sha256={self.manifest_sha256}\n"
            "query=nexus_root_start\npath_prefix=user/\nmatch_count=1\ntruncated=0\n"
            f"match={match.source_id}|{match.path}|{match.line}|{match.citation}|"
            f"{match.full_sha256}|{match.chunk_sha256}|{match.snippet}\n"
        )
        self.search = source.SearchAttestation(
            scope=source.SCOPE,
            corpus_revision=self.corpus_revision,
            manifest_sha256=self.manifest_sha256,
            query="nexus_root_start",
            path_prefix="user/",
            matches=(match,),
            truncated=False,
            projection=self.search_projection,
            projection_sha256=_sha_text(self.search_projection),
        )
        self.read = source.ReadAttestation(
            version=1,
            tool="source_read",
            scope=source.SCOPE,
            corpus_revision=self.corpus_revision,
            manifest_sha256=self.manifest_sha256,
            source_id=self.source_id,
            path=self.path,
            start_line=1,
            end_line=3,
            citation=self.citation,
            full_sha256=self.full_sha256,
            chunk_sha256=self.chunk_sha256,
            artifact_sha256=self.read_sha256,
            projection_sha256=self.read_sha256,
            content=self.read_content,
            projection=self.read_projection,
            corpus_bound_sha256="3" * 64,
        )

    def attest_search(self, query: object, path_prefix: object = "") -> source.SearchAttestation:
        if (query, path_prefix) != ("nexus_root_start", "user/"):
            raise source.SourceAttestationError("source search has no matches")
        return self.search

    def attest_read(
        self, source_id: object, start_line: object, end_line: object
    ) -> source.ReadAttestation:
        if (source_id, start_line, end_line) != (self.source_id, 1, 3):
            raise source.SourceAttestationError("source read range is invalid")
        return self.read

    def verify_evidence_event(self, event: object) -> bool:
        if not isinstance(event, Mapping):
            return False
        expected = {
            "scope": source.SCOPE,
            "corpus_revision": self.corpus_revision,
            "manifest_sha256": self.manifest_sha256,
            "source_id": self.source_id,
            "path": self.path,
            "start_line": 1,
            "end_line": 3,
            "citation": self.citation,
            "full_sha256": self.full_sha256,
            "chunk_sha256": self.chunk_sha256,
            "artifact_sha256": self.read_sha256,
            "projection_sha256": self.read_sha256,
        }
        return all(event.get(key) == value for key, value in expected.items())


class Scenario:
    def __init__(
        self,
        *,
        max_rounds: int = 16,
        retry_direct: bool = False,
        retry_final: bool = False,
        autonomous: bool = True,
        fatal: bool = False,
        cancel: bool = False,
        cancel_after_child: bool = False,
        cancel_with_tool_event: bool = False,
        cleanup_failure: bool = False,
        cleanup_tool_event: bool = False,
        source_miss_status: int = validator.SOURCE_SEARCH_NOT_FOUND_STATUS,
        source_miss_result: str = validator.SOURCE_SEARCH_NO_MATCHES_RESULT,
    ) -> None:
        self.attestor = SyntheticAttestor()
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
            }
        ]
        self.fixture: list[dict[str, object]] = []
        self.next_corr = 1
        self.turn_attempt = 0
        self.turn_decisions = 0
        self.turn_retries = 0
        self.retry_direct = retry_direct
        self.retry_final = retry_final
        self.source_miss_status = source_miss_status
        self.source_miss_result = source_miss_result
        self.next_task = 1000
        self.next_tool_sequence = 1
        self.next_handle_slot = 1
        self.tick = 100
        self.generation = 1
        self.ledger = ledger_module.NexusTaskLedger(require_kernel_identity=True)
        for role, (pid, agent_id, control_id) in IDENTITIES.items():
            self.ledger.set_kernel_identity(
                role=role, pid=pid, agent_id=agent_id, control_id=control_id
            )
        self._controls_before()
        self._direct_turn(1, 10, GOALS[0])
        if autonomous:
            self._autonomous_turn(2, 20, GOALS[1])
        if fatal:
            self._fatal_turn(3, 30, "Exercise a fatal provider outcome.")
        if cancel:
            self._cancel_turn(
                3,
                30,
                "Inspect runtime, but stop if I interrupt the active worker.",
                cancel_after_child=cancel_after_child,
                cancel_with_tool_event=cancel_with_tool_event,
                cleanup_failure=cleanup_failure,
                cleanup_tool_event=cleanup_tool_event,
            )
            if not cleanup_failure:
                self._direct_turn(4, 40, "Confirm the session recovered after cancellation.")
        self._controls_after(50 if cancel else 30)
        self.controller.extend(
            (
                {"type": "session_closing", "reason": "user_requested"},
                {
                    "type": "session_closed",
                    "reason": "session_error" if cleanup_failure else "guest_complete",
                },
            )
        )
        self.observer = self._observer()

    def _controls_before(self) -> None:
        self.controller.extend(
            (
                {
                    "type": "control_result",
                    "request_id": 1,
                    "command": "tools",
                    "status": "ok",
                    "result": {"tools": [copy.deepcopy(tool) for tool in contract.TOOLS]},
                },
                {
                    "type": "control_result",
                    "request_id": 2,
                    "command": "status",
                    "status": "ok",
                    "result": {
                        "tick": 50,
                        "loop_state": 1,
                        "call_count": 8,
                        "wait_sleep": 2,
                        "wait_wakeup": 2,
                        "capability_mask": 63,
                    },
                },
            )
        )

    def _controls_after(self, base: int) -> None:
        for offset, command in enumerate(("context", "tasks", "artifacts"), 1):
            self.controller.append(
                {
                    "type": "control_result",
                    "request_id": base + offset,
                    "command": command,
                    "status": "ok",
                    "result": {
                        "count": 4,
                        "oldest_sequence": 1,
                        "latest_sequence": 9,
                        "dropped": 0,
                        "provenance": 52,
                        "detail": f"{command}_snapshot",
                    },
                }
            )

    def _turn_start(self, turn: int, request: int, goal: str) -> int:
        self.turn_attempt = 0
        self.turn_decisions = 0
        self.turn_retries = 0
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
        root = 100 + turn
        for event, state, optional in (
            ("assigned", "assigned", {"summary": "user_goal_received"}),
            ("accepted", "accepted", {}),
            ("progress", "running", {"metric_code": 1, "metric_value": 8}),
        ):
            self._task_event(
                turn, request, corr, root, 0, event, state, "coordinator", **optional
            )
        return corr

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
        if parent == 0:
            route = (pid, pid)
        elif event == "assigned":
            route = (IDENTITIES["coordinator"][0], pid)
        else:
            route = (pid, IDENTITIES["coordinator"][0])
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
        successful: list[tuple[int, str, str]],
    ) -> dict[str, object]:
        bindings = [
            {"tool_corr_id": prior, "tool": tool, "projection_sha256": digest}
            for prior, tool, digest in successful[-4:]
        ]
        digest = f"{corr:064x}"
        raw_digest = f"{corr + 10_000:064x}"
        value = {
            "type": "model_request",
            "turn_id": turn,
            "request_id": request,
            "corr_id": corr,
            "round": round_number,
            "attempt": self.turn_attempt + 1,
            "request_sha256": digest,
            "raw_guest_request_sha256": raw_digest,
            "history_bindings": bindings,
            "request_contains_user": True,
            "user_message_index": 0,
            "generation": self.generation,
            "user_content_sha256": _sha_text(goal),
            "user_bytes": len(goal.encode("utf-8")),
        }
        self.turn_attempt += 1
        self.ledger.record_model_request(corr)
        self.controller.append(value)
        return value

    def _response(
        self,
        request_record: dict[str, object],
        response: dict[str, object],
        *,
        final_evidence_root: str | None = None,
    ) -> dict[str, object]:
        corr = int(request_record["corr_id"])
        fixture = {"request_sha256": request_record["request_sha256"], "response": response}
        self.fixture.append(fixture)
        if response["type"] == "tool_use":
            arguments = copy.deepcopy(response["arguments"])
            self.ledger.record_delivered_tool(
                corr,
                response["tool"],
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
            public.update({"tool": response["tool"], "arguments": copy.deepcopy(response["arguments"])})
        else:
            assert final_evidence_root is not None
            proof["final_request_sha256"] = request_record["request_sha256"]
            proof["final_evidence_root"] = final_evidence_root
            provider_proof_sha = validator._sha(proof)
            public.update(
                {
                    "content": response["content"],
                    "final_request_sha256": request_record["request_sha256"],
                    "final_evidence_root": final_evidence_root,
                    "final_response_sha256": response_sha,
                    "provider_proof_sha256": provider_proof_sha,
                }
            )
        self.controller.append(public)
        self.turn_decisions += 1
        return public

    def _model_error(
        self,
        request_record: dict[str, object],
        *,
        code: str = "BAD_PROVIDER_RESPONSE",
        retryable: bool = True,
    ) -> dict[str, object]:
        corr = int(request_record["corr_id"])
        response = {
            "type": "error",
            "code": code,
            "message": "replayed provider error",
            "retryable": retryable,
        }
        self.fixture.append(
            {
                "request_sha256": request_record["request_sha256"],
                "response": response,
            }
        )
        self.ledger.record_model_error(corr, retryable=retryable)
        if retryable:
            self.turn_retries += 1
        public = {
            "type": "model_error",
            "turn_id": request_record["turn_id"],
            "request_id": request_record["request_id"],
            "corr_id": corr,
            "code": code,
        }
        self.controller.append(public)
        return public

    def _child_tool(
        self,
        turn: int,
        request: int,
        corr: int,
        tool: str,
        *,
        projection: str = "",
        status: int = 0,
        result: str,
        report_handle: int = 0,
        evidence: bool = False,
    ) -> tuple[int, str]:
        task_id = self.next_task
        self.next_task += 1
        role = ledger_module.TASK_TOOL_ROLES[tool]
        pid, agent_id, _control = IDENTITIES[role]
        deadline = self.tick + 5000
        parent = 100 + turn
        self._task_event(
            turn, request, corr, task_id, parent, "assigned", "assigned", role,
            deadline_tick=deadline, summary=f"{tool}_assigned",
        )
        self._task_event(
            turn, request, corr, task_id, parent, "accepted", "accepted", role,
            deadline_tick=deadline,
        )
        self._task_event(
            turn, request, corr, task_id, parent, "progress", "running", role,
            deadline_tick=deadline, context_seq=7,
        )
        if status != 0:
            self._task_event(
                turn, request, corr, task_id, parent, "failed", "failed", role,
                deadline_tick=deadline, status=status, summary="task_failed;replan_allowed=1",
            )
            self._tool_event(
                turn, request, corr, tool, status=status, result=result,
            )
            return task_id, ""

        digest = _sha_text(projection)
        self._task_event(
            turn, request, corr, task_id, parent, "completed", "completed", role,
            deadline_tick=deadline,
        )
        artifact_optional: dict[str, object] = {
            "deadline_tick": deadline,
            "provenance": ledger_module.TOOL_PROVENANCE[tool],
            "resource_used": len(projection.encode("utf-8")),
            "digest": digest,
            "summary": "model_report_preserved" if tool == "draft_report" else "tool_evidence_ready",
        }
        if report_handle:
            artifact_optional["artifact_handle"] = report_handle
        self._task_event(
            turn, request, corr, task_id, parent,
            "artifact_published", "completed", role, **artifact_optional,
        )
        if evidence:
            read = self.attestor.read
            self.controller.append(
                {
                    "type": "evidence_event",
                    "version": 1,
                    "turn_id": turn,
                    "request_id": request,
                    "corr_id": corr,
                    "task_id": task_id,
                    "provenance": ledger_module.TOOL_PROVENANCE["source_read"],
                    "event": "source_read",
                    "tool": "source_read",
                    "scope": read.scope,
                    "corpus_revision": read.corpus_revision,
                    "manifest_sha256": read.manifest_sha256,
                    "source_id": read.source_id,
                    "path": read.path,
                    "start_line": read.start_line,
                    "end_line": read.end_line,
                    "citation": read.citation,
                    "full_sha256": read.full_sha256,
                    "chunk_sha256": read.chunk_sha256,
                    "artifact_sha256": read.artifact_sha256,
                    "projection_sha256": read.projection_sha256,
                }
            )
        self._tool_event(
            turn,
            request,
            corr,
            tool,
            status=0,
            result=result,
            projection=projection,
            values=(report_handle, task_id, agent_id),
            evidence_task_id=(task_id if evidence else 0),
        )
        return task_id, digest

    def _history_wrapper(
        self,
        tool: str,
        status: int,
        values: tuple[int, int, int],
        result: str,
        projection: str,
    ) -> dict[str, object]:
        wrapper: dict[str, object] = {
            "status": status,
            "value0": values[0],
        }
        if tool == "read_artifact":
            wrapper["value1_omitted"] = "volatile_payload_size"
        else:
            wrapper["value1"] = values[1]
        wrapper["value2"] = values[2]
        wrapper["result"] = result
        if projection:
            if tool in ("draft_report", "read_artifact"):
                wrapper["model_authored_content"] = projection
                wrapper["integrity_verified"] = True
                wrapper["content_trust"] = "untrusted_model_derived"
            elif tool == "source_search":
                wrapper["discovery_projection"] = projection
                wrapper["evidence_trust"] = "unverified_discovery_hint"
            elif tool == "source_read":
                wrapper["source_evidence"] = projection
                wrapper["evidence_trust"] = "corpus_attested"
            elif tool == "inspect_runtime":
                wrapper["runtime_observation"] = projection
                wrapper["evidence_trust"] = "guest_runtime_unattested"
        return wrapper

    def _tool_event(
        self,
        turn: int,
        request: int,
        corr: int,
        tool: str,
        *,
        status: int,
        result: str,
        projection: str = "",
        values: tuple[int, int, int] = (0, 0, 0),
        evidence_task_id: int = 0,
    ) -> dict[str, object]:
        wrapper = self._history_wrapper(tool, status, values, result, projection)
        event = {
            "type": "tool_event",
            "turn_id": turn,
            "request_id": request,
            "corr_id": corr,
            "tool": tool,
            "status": status,
            "sequence": 0,
            "value0": values[0],
            "value1": values[1],
            "value2": values[2],
            "result": result,
            "context_seq": self.next_tool_sequence + 10,
            "provenance": ledger_module.TOOL_PROVENANCE[tool] if status == 0 else 0,
            "projection_sha256": _sha_text(projection) if projection else "",
            "result_sha256": validator._sha(wrapper),
        }
        self.next_tool_sequence += 1
        evidence_values: dict[str, object] = {}
        if evidence_task_id:
            digest = _sha_text(projection)
            evidence_values = {
                "evidence_task_id": evidence_task_id,
                "evidence_provenance": ledger_module.TOOL_PROVENANCE["source_read"],
                "evidence_artifact_sha256": digest,
                "evidence_projection_sha256": digest,
            }
        self.ledger.settle_tool(
            corr,
            tool=tool,
            status=status,
            value0=values[0],
            value1=values[1],
            value2=values[2],
            provenance=event["provenance"],
            projection_sha256=event["projection_sha256"],
            result_sha256=event["result_sha256"],
            session_blocked_marker=(
                result if result == validator.ARTIFACT_CLEANUP_SESSION_BLOCK else ""
            ),
            **evidence_values,
        )
        self.controller.append(event)
        return event

    def _finish_completed(
        self,
        turn: int,
        request: int,
        corr: int,
        final_response: dict[str, object],
        evidence_root: str,
    ) -> None:
        self._task_event(
            turn, request, corr, 100 + turn, 0,
            "completed", "completed", "coordinator",
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
            "final_evidence_root": evidence_root,
            "final_task_root": snapshot.task_root_sha256,
            "final_artifact_root": snapshot.artifact_root_sha256,
        }
        self.controller.append(
            {
                "type": "turn_complete",
                "turn_id": turn,
                "request_id": request,
                "status": "completed",
                "answer": final_response["content"],
                "rounds": self.turn_decisions,
                "retries": self.turn_retries,
                "attempts": self.turn_attempt,
                **values,
                "final_proof_root": validator._sha(values),
            }
        )
        self.ledger.clear()
        self.generation += 2

    def _direct_turn(self, turn: int, request: int, goal: str) -> None:
        corr = self._turn_start(turn, request, goal)
        model_request = self._request(turn, request, goal, corr, 1, [])
        if self.retry_direct:
            self._model_error(model_request)
            corr += 1
            model_request = self._request(turn, request, goal, corr, 1, [])
        evidence_root = source.canonical_evidence_root([])
        response = self._response(
            model_request,
            {"type": "final", "content": "This task can be answered directly without kernel tools."},
            final_evidence_root=evidence_root,
        )
        self._finish_completed(turn, request, corr, response, evidence_root)
        self.next_corr = corr + 1

    def _autonomous_turn(self, turn: int, request: int, goal: str) -> None:
        corr = self._turn_start(turn, request, goal)
        successful: list[tuple[int, str, str]] = []
        round_number = 1

        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "source_search",
            "arguments": {"query": "definitely_missing_symbol", "path_prefix": "user/"},
        })
        self._child_tool(
            turn, request, corr, "source_search", status=self.source_miss_status,
            result=self.source_miss_result,
        )

        corr += 1; round_number += 1
        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "source_search",
            "arguments": {"query": "nexus_root_start", "path_prefix": "user/"},
        })
        _task, digest = self._child_tool(
            turn, request, corr, "source_search",
            projection=self.attestor.search_projection,
            result="source_evidence_ready;transient=1",
        )
        successful.append((corr, "source_search", digest))

        corr += 1; round_number += 1
        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "source_read",
            "arguments": {"source_id": "S0001", "start_line": 1, "max_lines": 3},
        })
        task, digest = self._child_tool(
            turn, request, corr, "source_read",
            projection=self.attestor.read_projection,
            result="source_evidence_ready;transient=1", evidence=True,
        )
        del task
        successful.append((corr, "source_read", digest))
        source_binding = self.attestor.read.evidence_binding(
            corr, self.next_task - 1, ledger_module.TOOL_PROVENANCE["source_read"]
        )

        corr += 1; round_number += 1
        runtime = (
            "scope=this_boot_guest_runtime\ncontent_untrusted=1\n"
            "operation=system_status\ntool=get_system_status\nstatus=0\n"
            "process_count=4\nagent_count=4\n"
            "volatile_fields_omitted=uptime_tick\n"
        )
        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "inspect_runtime",
            "arguments": {"operation": "system_status"},
        })
        _task, digest = self._child_tool(
            turn, request, corr, "inspect_runtime", projection=runtime,
            result="runtime_observation_ready;transient=1",
        )
        successful.append((corr, "inspect_runtime", digest))

        corr += 1; round_number += 1
        report = "\n".join(
            (
                "Bounded audit",
                "",
                "Scope",
                "- This report covers the current boot and bounded source snapshot.",
                "",
                "Source evidence",
                f"- The root task enters its explicit start path {self.attestor.citation}.",
                "- The citation is bound to the configured source corpus.",
                "",
                "Runtime evidence",
                "- The current boot is responsive.",
                "- Runtime scope is this boot only.",
                "",
                "Limitations",
                "- The source snapshot is bounded rather than the full Host tree.",
                "- Runtime observations do not describe another boot.",
                "",
                "Conclusion: the bounded source and runtime observations agree.",
            )
        )
        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "draft_report",
            "arguments": {"content": report, "title": "Bounded audit"},
        })
        handle = (LIFECYCLE_GENERATION << 16) | self.next_handle_slot
        self.next_handle_slot += 1
        _task, digest = self._child_tool(
            turn, request, corr, "draft_report", projection=report,
            result=f"report_drafted;handle={handle}", report_handle=handle,
        )
        successful.append((corr, "draft_report", digest))

        corr += 1; round_number += 1
        req = self._request(turn, request, goal, corr, round_number, successful)
        self._response(req, {
            "type": "tool_use", "tool": "read_artifact",
            "arguments": {"handle": handle},
        })
        self._tool_event(
            turn, request, corr, "read_artifact", status=0,
            result=f"report_read;handle={handle}", projection=report,
            values=(handle, len(report.encode("utf-8")), ledger_module.AGENT_NEXUS_ARTIFACT_REPORT),
        )
        successful.append((corr, "read_artifact", digest))

        corr += 1; round_number += 1
        req = self._request(turn, request, goal, corr, round_number, successful)
        if self.retry_final:
            self._model_error(req)
            corr += 1
            req = self._request(turn, request, goal, corr, round_number, successful)
        evidence_root = source.canonical_evidence_root([source_binding])
        response = self._response(
            req,
            {
                "type": "final",
                "content": (
                    "Bounded conclusion\n\n"
                    "The bounded source snapshot shows the explicit root start path "
                    f"{self.attestor.citation}.\n"
                    "This boot is responsive, and the report was re-read byte for byte."
                ),
            },
            final_evidence_root=evidence_root,
        )
        self._finish_completed(turn, request, corr, response, evidence_root)
        self.next_corr = corr + 1

    def _fatal_turn(self, turn: int, request: int, goal: str) -> None:
        corr = self._turn_start(turn, request, goal)
        req = self._request(turn, request, goal, corr, 1, [])
        self._model_error(req, code="PROVIDER_FAILURE", retryable=False)
        self.ledger.begin_termination(corr, "provider_fatal")
        self._task_event(
            turn,
            request,
            corr,
            100 + turn,
            0,
            "failed",
            "failed",
            "coordinator",
            status=ledger_module.AGENT_STATUS_IO_ERROR,
            summary="provider_fatal",
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
            "final_evidence_root": "",
            "final_task_root": snapshot.task_root_sha256,
            "final_artifact_root": snapshot.artifact_root_sha256,
        }
        self.controller.append(
            {
                "type": "turn_complete",
                "turn_id": turn,
                "request_id": request,
                "status": "error",
                "rounds": self.turn_decisions,
                "retries": self.turn_retries,
                "attempts": self.turn_attempt,
                **values,
                "final_proof_root": validator._sha(values),
            }
        )
        self.ledger.clear()
        self.generation += 2
        self.next_corr = corr + 1

    def _cancel_turn(
        self,
        turn: int,
        request: int,
        goal: str,
        *,
        cancel_after_child: bool,
        cancel_with_tool_event: bool,
        cleanup_failure: bool,
        cleanup_tool_event: bool,
    ) -> None:
        corr = self._turn_start(turn, request, goal)
        req = self._request(turn, request, goal, corr, 1, [])
        self._response(req, {
            "type": "tool_use", "tool": "inspect_runtime",
            "arguments": {"operation": "processes"},
        })
        task_id = self.next_task
        self.next_task += 1
        role = "system"
        deadline = self.tick + 5000
        parent = 100 + turn
        for event, state in (
            ("assigned", "assigned"), ("accepted", "accepted"), ("progress", "running")
        ):
            self._task_event(
                turn, request, corr, task_id, parent, event, state, role,
                deadline_tick=deadline,
                **({"context_seq": 11} if event == "progress" else {}),
            )
        def begin_cancel() -> None:
            self.ledger.begin_cancel(corr)
            self.controller.append(
                {"type": "turn_cancelling", "turn_id": turn, "request_id": request}
            )

        if not cancel_after_child:
            begin_cancel()
        self._task_event(
            turn, request, corr, task_id, parent,
            "cancelled", "cancelled", role,
            deadline_tick=deadline,
            status=ledger_module.AGENT_STATUS_CANCELLED,
            summary="task_cancelled;terminal_ack=1",
        )
        if cancel_after_child:
            begin_cancel()
        if cancel_with_tool_event:
            self._tool_event(
                turn,
                request,
                corr,
                "inspect_runtime",
                status=ledger_module.AGENT_STATUS_TIMEOUT,
                result="task_deadline;replan_allowed=1",
            )
        elif not cleanup_failure or not cleanup_tool_event:
            self.ledger.settle_cancelled_tool_from_task(corr)
        if cleanup_failure:
            self._task_event(
                turn, request, corr, 100 + turn, 0,
                "failed", "failed", "coordinator",
                status=ledger_module.AGENT_STATUS_IO_ERROR,
                summary=validator.ARTIFACT_CLEANUP_SESSION_BLOCK,
            )
            if cleanup_tool_event:
                self._tool_event(
                    turn,
                    request,
                    corr,
                    "inspect_runtime",
                    status=ledger_module.AGENT_STATUS_IO_ERROR,
                    result=validator.ARTIFACT_CLEANUP_SESSION_BLOCK,
                )
            turn_status = "error"
        else:
            self._task_event(
                turn, request, corr, 100 + turn, 0,
                "cancelled", "cancelled", "coordinator",
                status=ledger_module.AGENT_STATUS_CANCELLED,
                summary="turn_cancelled",
            )
            turn_status = "cancelled"
        snapshot = self.ledger.assert_turn_complete(turn_status)
        values = {
            "version": 1,
            "turn_id": turn,
            "request_id": request,
            "final_corr_id": corr,
            "final_request_sha256": "",
            "final_response_sha256": "",
            "provider_proof_sha256": "",
            "final_evidence_root": "",
            "final_task_root": snapshot.task_root_sha256,
            "final_artifact_root": snapshot.artifact_root_sha256,
        }
        self.controller.append(
            {
                "type": "turn_complete",
                "turn_id": turn,
                "request_id": request,
                "status": turn_status,
                "rounds": self.turn_decisions,
                "retries": self.turn_retries,
                "attempts": self.turn_attempt,
                **values,
                "final_proof_root": validator._sha(values),
            }
        )
        self.ledger.clear()
        self.generation += 3
        self.next_corr += 1

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
        for index, (role, (pid, agent_id, control_id)) in enumerate(IDENTITIES.items(), 1):
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
    def setUp(self) -> None:
        self.scenario = Scenario()

    def validate(
        self,
        scenario: Scenario | None = None,
        *,
        require_acceptance_scenarios: bool = True,
        goals: tuple[str, ...] | None = GOALS,
    ) -> validator.ValidationSummary:
        value = scenario or self.scenario
        return validator.validate_records(
            value.controller,
            value.observer,
            value.fixture,
            source_attestor_value=value.attestor,  # type: ignore[arg-type]
            goals=goals,
            require_acceptance_scenarios=require_acceptance_scenarios,
        )

    def rejected(self, mutate) -> None:
        scenario = copy.deepcopy(self.scenario)
        mutate(scenario)
        with self.assertRaises(validator.ValidationError):
            self.validate(scenario)

    def test_generic_two_task_trace_passes(self) -> None:
        summary = self.validate()
        self.assertEqual(summary.fixture_records, 8)
        self.assertTrue(summary.turns[0].direct_final)
        self.assertTrue(summary.turns[1].source_recovered)
        self.assertTrue(summary.turns[1].runtime)
        self.assertTrue(summary.turns[1].report_roundtrip)
        self.assertEqual(
            {tool for _corr, tool in summary.turns[1].tool_calls},
            set(validator.TOOL_NAMES),
        )

    def test_multiline_draft_report_roundtrips_exactly(self) -> None:
        self.validate()
        draft = next(
            item
            for item in self.scenario.fixture
            if item["response"].get("type") == "tool_use"  # type: ignore[union-attr]
            and item["response"].get("tool") == "draft_report"  # type: ignore[union-attr]
        )["response"]
        assert isinstance(draft, dict)
        arguments = draft["arguments"]
        assert isinstance(arguments, dict)
        report = arguments["content"]
        assert isinstance(report, str)
        self.assertEqual(report.count("\n"), 17)
        digest = _sha_text(report)
        projections = [
            item["projection_sha256"]
            for item in self.scenario.controller
            if item.get("type") == "tool_event"
            and item.get("tool") in ("draft_report", "read_artifact")
        ]
        self.assertEqual(projections, [digest, digest])

    def test_multiline_final_roundtrips_fixture_response_and_terminal(self) -> None:
        self.validate()
        fixture_final = self.scenario.fixture[-1]["response"]
        assert isinstance(fixture_final, dict)
        content = fixture_final["content"]
        assert isinstance(content, str)
        self.assertEqual(content.count("\n"), 3)
        response = [
            item
            for item in self.scenario.controller
            if item.get("type") == "model_response"
            and item.get("response_type") == "final"
        ][-1]
        terminal = [
            item
            for item in self.scenario.controller
            if item.get("type") == "turn_complete"
        ][-1]
        self.assertEqual(response["content"], content)
        self.assertEqual(terminal["answer"], content)

    def test_final_text_matches_provider_scalar_utf8_contract(self) -> None:
        import guest_llm_relay

        cases = (
            ("line 1\nline 2", 20, True),
            ("\x01\x7f\u0080", 4, True),
            ("\u00e9" * 6, 12, True),
            ("\u00e9" * 7, 12, False),
            ("\0", 12, False),
            ("\ud800", 12, False),
            ("", 12, False),
        )
        for value, maximum, accepted in cases:
            with self.subTest(value=repr(value), maximum=maximum):
                host_ok = True
                try:
                    guest_llm_relay._validate_final_content(
                        value, max_bytes=maximum
                    )
                except Exception:
                    host_ok = False
                validator_ok = True
                try:
                    validator._bounded_final_text(value, "final", maximum)
                except validator.ValidationError:
                    validator_ok = False
                self.assertEqual((host_ok, validator_ok), (accepted, accepted))
        with self.assertRaises(validator.ValidationError):
            validator._bounded_text("metadata\n", "metadata", 32)

    def test_tool_text_matches_host_raw_and_escaped_budgets(self) -> None:
        import agentos_relayd

        cases = (
            ("line 1\nline 2", 20, False, True),
            ("\n" * 2, 12, False, True),
            ("\n" * 3, 12, False, False),
            ('"' * 6, 12, False, True),
            ('"' * 7, 12, False, False),
            ("\u00e9" * 6, 12, False, True),
            ("\u00e9" * 7, 12, False, False),
            ("\x01", 6, False, True),
            ("\x7f\u0080", 3, False, True),
            ("\0", 12, False, False),
            ("\ud800", 12, False, False),
            ("", 12, False, False),
            ("", 12, True, True),
        )
        for value, maximum, empty, accepted in cases:
            with self.subTest(value=repr(value), maximum=maximum, empty=empty):
                host_ok = True
                try:
                    agentos_relayd._nexus_tool_text(
                        value, "test", maximum=maximum, empty=empty
                    )
                except Exception:
                    host_ok = False
                validator_ok = True
                try:
                    validator._bounded_tool_text(
                        value, "test", maximum, empty=empty
                    )
                except validator.ValidationError:
                    validator_ok = False
                self.assertEqual((host_ok, validator_ok), (accepted, accepted))

        validator._validate_tool_arguments(
            "draft_report",
            {"content": "\n" * 466, "title": "\n" * 21},
        )
        validator._validate_tool_arguments(
            "source_search", {"query": "\n" * 15, "path_prefix": "\x01"}
        )
        for tool, arguments in (
            ("draft_report", {"content": "\n" * 467}),
            ("draft_report", {"content": "ok", "title": "\n" * 22}),
            ("source_search", {"query": "\n" * 16}),
        ):
            with self.subTest(tool=tool), self.assertRaises(validator.ValidationError):
                validator._validate_tool_arguments(tool, arguments)
        with self.assertRaises(validator.ValidationError):
            validator._bounded_text("metadata\n", "metadata", 32)

    def test_multiple_tool_events_use_the_guest_sequence_zero(self) -> None:
        self.validate()
        events = [
            item for item in self.scenario.controller if item.get("type") == "tool_event"
        ]
        self.assertGreater(len(events), 1)
        self.assertEqual({item["sequence"] for item in events}, {0})
        validator._validate_tool_event_shape(events[0])
        malformed = copy.deepcopy(events[0])
        malformed["sequence"] = -1
        with self.assertRaises(validator.ValidationError):
            validator._validate_tool_event_shape(malformed)

    def test_retryable_fixture_error_reuses_decision_slot_and_preserves_history(self) -> None:
        scenario = Scenario(retry_final=True)
        summary = self.validate(scenario)
        self.assertEqual(summary.fixture_records, 9)
        self.assertEqual(summary.turns[1].request_count, 8)
        requests = [
            item
            for item in scenario.controller
            if item.get("type") == "model_request" and item.get("turn_id") == 2
        ]
        self.assertEqual(
            [(item["round"], item["attempt"]) for item in requests[-2:]],
            [(7, 7), (7, 8)],
        )
        self.assertEqual(
            requests[-2]["history_bindings"], requests[-1]["history_bindings"]
        )
        terminal = [
            item
            for item in scenario.controller
            if item.get("type") == "turn_complete" and item.get("turn_id") == 2
        ][0]
        self.assertEqual(
            (terminal["rounds"], terminal["retries"], terminal["attempts"]),
            (7, 1, 8),
        )

    def test_retry_mutations_fail_closed(self) -> None:
        def retry_scenario() -> Scenario:
            return Scenario(retry_final=True)

        def rejected(mutate) -> None:
            value = retry_scenario()
            mutate(value)
            value.observer = value._observer()
            with self.assertRaises(validator.ValidationError):
                self.validate(value)

        rejected(
            lambda value: next(
                item
                for item in value.controller
                if item.get("type") == "model_error"
            ).__setitem__("code", "DIFFERENT_ERROR")
        )
        rejected(
            lambda value: next(
                item
                for item in value.controller
                if item.get("type") == "model_error"
            ).__setitem__("message", "unsafe")
        )
        rejected(
            lambda value: [
                item
                for item in value.controller
                if item.get("type") == "model_request" and item.get("turn_id") == 2
            ][-1].__setitem__("round", 8)
        )
        rejected(
            lambda value: [
                item
                for item in value.controller
                if item.get("type") == "model_request" and item.get("turn_id") == 2
            ][-1].__setitem__("attempt", 9)
        )
        rejected(
            lambda value: next(
                item
                for item in value.fixture
                if item["response"].get("type") == "error"  # type: ignore[union-attr]
            ).__setitem__("request_sha256", value.fixture[0]["request_sha256"])
        )
        rejected(
            lambda value: next(
                item["response"]
                for item in value.fixture
                if item["response"].get("type") == "error"  # type: ignore[union-attr]
            ).__setitem__("retryable", False)  # type: ignore[union-attr]
        )
        rejected(
            lambda value: value.fixture.pop(
                next(
                    index
                    for index, item in enumerate(value.fixture)
                    if item["response"].get("type") == "error"  # type: ignore[union-attr]
                )
            )
        )

        for field, replacement in (
            ("rounds", 6),
            ("retries", 0),
            ("attempts", 7),
            ("rounds", True),
            ("retries", True),
            ("attempts", True),
        ):
            with self.subTest(counter=field, replacement=replacement):
                rejected(
                    lambda value, key=field, item=replacement: next(
                        record
                        for record in value.controller
                        if record.get("type") == "turn_complete"
                        and record.get("turn_id") == 2
                    ).__setitem__(key, item)
                )

        for field in ("rounds", "retries", "attempts"):
            with self.subTest(missing_counter=field):
                rejected(
                    lambda value, key=field: next(
                        record
                        for record in value.controller
                        if record.get("type") == "turn_complete"
                        and record.get("turn_id") == 2
                    ).pop(key)
                )

    def test_negotiated_one_decision_allows_retry_then_final(self) -> None:
        scenario = Scenario(
            max_rounds=1,
            retry_direct=True,
            autonomous=False,
        )
        summary = self.validate(
            scenario,
            goals=(GOALS[0],),
            require_acceptance_scenarios=False,
        )
        self.assertEqual(summary.fixture_records, 2)
        self.assertEqual(summary.turns[0].request_count, 2)
        requests = [
            item for item in scenario.controller if item.get("type") == "model_request"
        ]
        self.assertEqual(
            [(item["round"], item["attempt"]) for item in requests],
            [(1, 1), (1, 2)],
        )
        terminal = next(
            item for item in scenario.controller if item.get("type") == "turn_complete"
        )
        self.assertEqual(
            (terminal["rounds"], terminal["retries"], terminal["attempts"]),
            (1, 1, 2),
        )

    def test_fatal_fixture_error_arms_provider_fatal_without_retry(self) -> None:
        scenario = Scenario(fatal=True)
        summary = self.validate(
            scenario,
            goals=None,
            require_acceptance_scenarios=False,
        )
        self.assertEqual(summary.turns[-1].status, "error")
        self.assertEqual(summary.turns[-1].request_count, 1)
        terminal = [
            item for item in scenario.controller if item.get("type") == "turn_complete"
        ][-1]
        self.assertEqual(
            (terminal["rounds"], terminal["retries"], terminal["attempts"]),
            (0, 0, 1),
        )

        error = scenario.fixture[-1]["response"]
        assert isinstance(error, dict)
        error["retryable"] = True
        scenario.observer = scenario._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(
                scenario,
                goals=None,
                require_acceptance_scenarios=False,
            )

    def test_deepseek_error_receipt_is_exact_and_binds_model_error(self) -> None:
        scenario = Scenario(retry_final=True)
        error = next(
            item for item in scenario.controller if item.get("type") == "model_error"
        )
        request = next(
            item
            for item in scenario.controller
            if item.get("type") == "model_request"
            and item.get("corr_id") == error["corr_id"]
        )
        proof = {
            "type": "provider_result",
            "turn_id": request["turn_id"],
            "request_id": request["request_id"],
            "corr_id": request["corr_id"],
            "status": "error",
            "code": error["code"],
            "generation": request["generation"],
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "request_sha256": request["request_sha256"],
            "raw_guest_request_sha256": request["raw_guest_request_sha256"],
            "history_bindings": copy.deepcopy(request["history_bindings"]),
            "request_contains_user": True,
            "user_message_index": 0,
            "user_content_sha256": request["user_content_sha256"],
            "user_bytes": request["user_bytes"],
            "adapter_success": False,
            "transport": "https",
            "provider_endpoint": "https://api.deepseek.com/chat/completions",
            "http_status": 200,
            "provider_request_sha256": "a" * 64,
            "provider_response_sha256": "b" * 64,
            "selected_reply_sha256": "",
            "attempt_count": 1,
            "tool_choice_mode": "auto",
            "raw_tool_call_count": 2,
            "selected_index": -1,
            "adaptation": "rejected_bad_provider_response",
            "forced_tool": None,
            "selected_tool_sha256": "",
        }
        validator._validate_provider_error_proof(
            proof,
            request,
            error,
            provider="deepseek",
            model="deepseek-v4-flash",
        )
        for field, replacement in (
            ("code", "DIFFERENT_ERROR"),
            ("adapter_success", True),
            ("selected_reply_sha256", "c" * 64),
            ("attempt_count", 2),
            ("tool_choice_mode", "exact"),
            ("adaptation", "none"),
        ):
            malformed = copy.deepcopy(proof)
            malformed[field] = replacement
            with self.subTest(field=field), self.assertRaises(validator.ValidationError):
                validator._validate_provider_error_proof(
                    malformed,
                    request,
                    error,
                    provider="deepseek",
                    model="deepseek-v4-flash",
                )

    def test_fixture_error_schema_is_exact_bounded_and_safe(self) -> None:
        canonical: dict[str, object] = {
            "type": "error",
            "code": "BAD_PROVIDER_RESPONSE",
            "message": "replayed provider error",
            "retryable": True,
        }
        accepted = [
            {
                "request_sha256": "f" * 64,
                "response": canonical,
            },
            *copy.deepcopy(self.scenario.fixture),
        ]
        self.assertEqual(len(validator._validate_fixture(accepted)), 9)
        invalid = (
            canonical | {"detail": "secret"},
            {key: value for key, value in canonical.items() if key != "message"},
            canonical | {"public_message": "alias"},
            canonical | {"code": "bad_provider_response"},
            canonical | {"code": "X" * 65},
            canonical | {"message": "bad\ntext"},
            canonical | {"message": "x" * 241},
            canonical | {"retryable": 1},
        )
        for response in invalid:
            with self.subTest(response=response):
                records = copy.deepcopy(accepted)
                records[0]["response"] = response
                with self.assertRaises(validator.ValidationError):
                    validator._validate_fixture(records)

    def test_fixture_retry_normalization_matches_the_host(self) -> None:
        import agentos_relayd

        self.assertEqual(
            validator.NEXUS_RETRYABLE_RESPONSE_ERROR_CODES,
            agentos_relayd._NEXUS_RETRYABLE_RESPONSE_ERROR_CODES,
        )
        for code in validator.NEXUS_RETRYABLE_RESPONSE_ERROR_CODES:
            records = [
                {
                    "request_sha256": "f" * 64,
                    "response": {
                        "type": "error",
                        "code": code,
                        "message": "replayed provider error",
                        "retryable": False,
                    },
                },
                *copy.deepcopy(self.scenario.fixture),
            ]
            with self.subTest(code=code), self.assertRaises(validator.ValidationError):
                validator._validate_fixture(records)
            records[0]["response"]["retryable"] = True  # type: ignore[index]
            self.assertEqual(len(validator._validate_fixture(records)), 9)

    def test_session_retry_budget_is_exact(self) -> None:
        self.assertEqual(validator.MAX_RETRIES, 32)
        self.assertEqual(validator.MAX_PROVIDER_ATTEMPTS, 48)
        for value in (None, 0, validator.MAX_RETRIES - 1,
                      validator.MAX_RETRIES + 1, True):
            scenario = copy.deepcopy(self.scenario)
            if value is None:
                del scenario.controller[1]["max_retries"]
            else:
                scenario.controller[1]["max_retries"] = value
            scenario.observer = scenario._observer()
            with self.subTest(max_retries=value), self.assertRaises(
                validator.ValidationError
            ):
                self.validate(scenario)

        for value in (None, 0, 17, True):
            scenario = copy.deepcopy(self.scenario)
            if value is None:
                del scenario.controller[1]["max_rounds"]
            else:
                scenario.controller[1]["max_rounds"] = value
            scenario.observer = scenario._observer()
            with self.subTest(max_rounds=value), self.assertRaises(
                validator.ValidationError
            ):
                self.validate(scenario)

    def test_host_generation_fences_turn_completion(self) -> None:
        scenario = Scenario()
        starts = [
            item for item in scenario.controller if item.get("type") == "turn_started"
        ]
        self.assertEqual([item["generation"] for item in starts], [1, 3])
        starts[1]["generation"] = 2
        scenario.observer = scenario._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(scenario)

    def test_history_window_counts_failed_tool_turns(self) -> None:
        settled = [
            (1, "source_search", "1" * 64),
            (2, "source_read", "2" * 64),
            (3, "inspect_runtime", "3" * 64),
            (4, "draft_report", "4" * 64),
            None,
        ]
        expected = [
            {"tool_corr_id": corr, "tool": tool, "projection_sha256": digest}
            for corr, tool, digest in settled[1:4]  # type: ignore[misc]
        ]
        self.assertEqual(
            validator._history_bindings(
                expected, current_corr=6, settled=settled
            ),
            tuple(expected),
        )
        stale = [
            {"tool_corr_id": corr, "tool": tool, "projection_sha256": digest}
            for corr, tool, digest in settled[:4]  # type: ignore[misc]
        ]
        with self.assertRaises(validator.ValidationError):
            validator._history_bindings(stale, current_corr=6, settled=settled)

    def test_contract_is_exactly_five_autonomous_tools(self) -> None:
        self.assertEqual(
            validator.TOOL_NAMES,
            ("source_search", "source_read", "inspect_runtime", "draft_report", "read_artifact"),
        )
        rendered = str(self.scenario.fixture).lower()
        for obsolete in validator.STALE_PATTERNS:
            self.assertNotIn(obsolete, rendered)

    def test_control_identity_accepts_the_full_u64_abi_only(self) -> None:
        task = next(
            copy.deepcopy(item)
            for item in self.scenario.controller
            if item.get("type") == "task_event"
        )
        for control_id in (1 << 63, validator.U64_MAX):
            task["control_id"] = control_id
            task["agent_control_id"] = control_id
            self.assertEqual(
                validator._task_identity_fields(task)[3], control_id
            )
            projection = validator._observer_projection(task, source="guest")
            self.assertEqual(projection["control_id"], control_id)
            self.assertEqual(projection["agent_control_id"], control_id)

        observer = copy.deepcopy(self.scenario.observer)
        expected: dict[str, int] = {}
        index = 0
        for record in observer:
            if record.get("source") not in ("kernel_audit", "kernel_snapshot"):
                continue
            role = record.get("role")
            if role not in validator.BUSINESS_ROLES:
                continue
            control_id = (1 << 63) if index % 2 == 0 else validator.U64_MAX
            record["actor_control_id"] = control_id
            expected[str(role)] = control_id
            index += 1
        identities = validator._kernel_identities(observer)
        self.assertEqual(
            {identity.role: identity.control_id for identity in identities.values()},
            expected,
        )

        overflow = 1 << 64
        task["control_id"] = overflow
        task["agent_control_id"] = overflow
        with self.assertRaises(validator.ValidationError):
            validator._task_identity_fields(task)
        projection = validator._observer_projection(task, source="guest")
        self.assertNotIn("control_id", projection)
        self.assertNotIn("agent_control_id", projection)
        bad_observer = copy.deepcopy(observer)
        kernel = next(
            record
            for record in bad_observer
            if record.get("source") in ("kernel_audit", "kernel_snapshot")
            and record.get("role") in validator.BUSINESS_ROLES
        )
        kernel["actor_control_id"] = overflow
        with self.assertRaises(validator.ValidationError):
            validator._kernel_identities(bad_observer)

    def test_full_replay_accepts_high_u64_control_identities(self) -> None:
        original = dict(IDENTITIES)
        try:
            for controls in (
                tuple((1 << 63) + index for index in range(4)),
                tuple(validator.U64_MAX - index for index in range(4)),
            ):
                IDENTITIES.clear()
                IDENTITIES.update(
                    {
                        role: (pid, agent_id, controls[index])
                        for index, (role, (pid, agent_id, _control))
                        in enumerate(original.items())
                    }
                )
                summary = self.validate(Scenario())
                self.assertEqual(len(summary.turns), 2)
        finally:
            IDENTITIES.clear()
            IDENTITIES.update(original)

    def test_fixture_rejects_unadvertised_or_forced_business_route(self) -> None:
        self.rejected(lambda value: value.fixture[1]["response"].__setitem__("tool", "delegate_task"))  # type: ignore[union-attr]
        self.rejected(lambda value: value.fixture[2]["response"].__setitem__("role", "research"))  # type: ignore[union-attr]

    def test_fixture_request_sha_is_strict_and_unique(self) -> None:
        self.rejected(lambda value: value.fixture[1].__setitem__("request_sha256", value.fixture[0]["request_sha256"]))
        self.rejected(lambda value: value.fixture[0].__setitem__("request_sha256", "0" * 63))
        self.rejected(lambda value: value.controller[9].__setitem__("request_sha256", "f" * 64))

    def test_root_ready_prelude_must_precede_first_model_request(self) -> None:
        def swap(value: Scenario) -> None:
            started = next(i for i, item in enumerate(value.controller) if item.get("type") == "turn_started")
            request = next(i for i in range(started, len(value.controller)) if value.controller[i].get("type") == "model_request")
            value.controller[started + 1], value.controller[request] = value.controller[request], value.controller[started + 1]
            value.observer = value._observer()
        self.rejected(swap)

    def test_each_model_request_has_one_ordered_outcome(self) -> None:
        def duplicate(value: Scenario) -> None:
            index = next(i for i, item in enumerate(value.controller) if item.get("type") == "model_response")
            value.controller.insert(index + 1, copy.deepcopy(value.controller[index]))
            value.observer = value._observer()
        self.rejected(duplicate)

    def test_model_round_and_session_correlation_budgets_are_enforced(self) -> None:
        def bad_round(value: Scenario) -> None:
            request = next(item for item in value.controller if item.get("type") == "model_request")
            request["round"] = 17
            value.observer = value._observer()
        self.rejected(bad_round)
        def reused(value: Scenario) -> None:
            requests = [item for item in value.controller if item.get("type") == "model_request"]
            requests[1]["corr_id"] = requests[0]["corr_id"]
            value.observer = value._observer()
        self.rejected(reused)

    def test_history_tail_is_complete_adjacent_and_digest_bound(self) -> None:
        def drop(value: Scenario) -> None:
            requests = [item for item in value.controller if item.get("type") == "model_request"]
            target = next(item for item in requests if len(item["history_bindings"]) >= 2)  # type: ignore[arg-type]
            target["history_bindings"].pop(0)  # type: ignore[union-attr]
            response = next(item for item in value.controller if item.get("type") == "model_response" and item.get("corr_id") == target["corr_id"])
            response["history_bindings"] = copy.deepcopy(target["history_bindings"])
            value.observer = value._observer()
        self.rejected(drop)

    def test_task_dag_identity_route_and_terminal_are_replayed(self) -> None:
        def route(value: Scenario) -> None:
            child = next(item for item in value.controller if item.get("type") == "task_event" and item.get("parent_task_id") != 0)
            child["source_pid"], child["target_pid"] = child["target_pid"], child["source_pid"]
            value.observer = value._observer()
        self.rejected(route)
        def terminal(value: Scenario) -> None:
            child = next(item for item in value.controller if item.get("type") == "task_event" and item.get("event") == "failed")
            child["status"] = 0
            value.observer = value._observer()
        self.rejected(terminal)

    def test_source_miss_requires_exact_typed_not_found_settlement(self) -> None:
        for status in (-2, ledger_module.AGENT_STATUS_TIMEOUT):
            scenario = Scenario(source_miss_status=status)
            with self.subTest(status=status), self.assertRaises(
                validator.ValidationError
            ):
                self.validate(scenario)

        for result in (
            "task_failed;replan_allowed=1",
            "source_search_no_matches",
            validator.SOURCE_SEARCH_NO_MATCHES_RESULT + ";extra=1",
        ):
            scenario = Scenario(source_miss_result=result)
            with self.subTest(result=result), self.assertRaises(
                validator.ValidationError
            ):
                self.validate(scenario)

    def test_source_miss_requires_the_host_attestor_no_match_reason(self) -> None:
        class InvalidSearchAttestor(SyntheticAttestor):
            def attest_search(
                self, query: object, path_prefix: object = ""
            ) -> source.SearchAttestation:
                if query == "definitely_missing_symbol":
                    raise source.SourceAttestationError(
                        "source search prefix is non-canonical"
                    )
                return super().attest_search(query, path_prefix)

        scenario = Scenario()
        scenario.attestor = InvalidSearchAttestor()
        with self.assertRaises(validator.ValidationError):
            self.validate(scenario)

    def test_source_miss_is_rejected_when_the_host_corpus_has_matches(self) -> None:
        class UnexpectedMatchAttestor(SyntheticAttestor):
            def attest_search(
                self, query: object, path_prefix: object = ""
            ) -> source.SearchAttestation:
                if query == "definitely_missing_symbol":
                    return self.search
                return super().attest_search(query, path_prefix)

        scenario = Scenario()
        scenario.attestor = UnexpectedMatchAttestor()
        with self.assertRaises(validator.ValidationError):
            self.validate(scenario)

    def test_source_miss_rejects_success_bindings_and_wrong_result_digest(self) -> None:
        mutations: tuple[tuple[str, object], ...] = (
            ("value0", 1),
            ("value1", 1),
            ("value2", 1),
            ("provenance", validator.SUCCESS_PROVENANCE["source_search"]),
            ("projection_sha256", "f" * 64),
            ("result_sha256", "f" * 64),
        )
        for field, replacement in mutations:
            scenario = Scenario()
            event = next(
                item
                for item in scenario.controller
                if item.get("type") == "tool_event"
                and item.get("tool") == "source_search"
                and item.get("status") != 0
            )
            event[field] = replacement
            scenario.observer = scenario._observer()
            with self.subTest(field=field), self.assertRaises(
                validator.ValidationError
            ):
                self.validate(scenario)

    def test_source_success_binds_visible_result_and_canonical_wrapper(self) -> None:
        for mutation in ("contradictory_result", "rehashed_contradiction", "digest"):
            scenario = Scenario()
            event = next(
                item
                for item in scenario.controller
                if item.get("type") == "tool_event"
                and item.get("tool") == "source_search"
                and item.get("status") == 0
            )
            if mutation in ("contradictory_result", "rehashed_contradiction"):
                event["result"] = validator.SOURCE_SEARCH_NO_MATCHES_RESULT
            if mutation == "rehashed_contradiction":
                event["result_sha256"] = validator._sha(
                    scenario._history_wrapper(
                        "source_search",
                        0,
                        (
                            int(event["value0"]),
                            int(event["value1"]),
                            int(event["value2"]),
                        ),
                        str(event["result"]),
                        scenario.attestor.search_projection,
                    )
                )
            elif mutation == "digest":
                event["result_sha256"] = "f" * 64
            scenario.observer = scenario._observer()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                validator.ValidationError, "exact success projection"
            ):
                self.validate(scenario)

    def test_source_evidence_requires_exact_host_attestation(self) -> None:
        def mutate(value: Scenario) -> None:
            evidence = next(item for item in value.controller if item.get("type") == "evidence_event")
            evidence["citation"] = "[S0001:L1-L2]"
            value.observer = value._observer()
        self.rejected(mutate)

    def test_source_evidence_binds_task_tool_and_final_root(self) -> None:
        def task(value: Scenario) -> None:
            evidence = next(item for item in value.controller if item.get("type") == "evidence_event")
            evidence["task_id"] = int(evidence["task_id"]) + 1
            value.observer = value._observer()
        self.rejected(task)
        def root(value: Scenario) -> None:
            final = [item for item in value.controller if item.get("type") == "turn_complete"][-1]
            final["final_evidence_root"] = "0" * 64
            value.observer = value._observer()
        self.rejected(root)

    def test_final_citation_must_come_from_final_request_evidence(self) -> None:
        def invented(value: Scenario) -> None:
            final = [item for item in value.controller if item.get("type") == "model_response" and item.get("response_type") == "final"][-1]
            final["content"] = "Invented [S9999:L1-L1]"
            value.observer = value._observer()
        self.rejected(invented)

    def test_runtime_is_separate_unattested_projection(self) -> None:
        def provenance(value: Scenario) -> None:
            runtime = next(item for item in value.controller if item.get("type") == "tool_event" and item.get("tool") == "inspect_runtime")
            runtime["provenance"] = 60
            value.observer = value._observer()
        self.rejected(provenance)

    def test_transient_handles_do_not_escape(self) -> None:
        def handle(value: Scenario) -> None:
            event = next(item for item in value.controller if item.get("type") == "task_event" and item.get("event") == "artifact_published" and item.get("role") == "research")
            event["artifact_handle"] = (1 << 16) | 55
            value.observer = value._observer()
        self.rejected(handle)

    def test_report_readback_is_exact_model_bytes_and_current_handle(self) -> None:
        def digest(value: Scenario) -> None:
            read = next(item for item in value.controller if item.get("type") == "tool_event" and item.get("tool") == "read_artifact")
            read["projection_sha256"] = "f" * 64
            value.observer = value._observer()
        self.rejected(digest)
        def stale(value: Scenario) -> None:
            response = next(item for item in value.controller if item.get("type") == "model_response" and item.get("tool") == "read_artifact")
            response["arguments"]["handle"] = 123  # type: ignore[index]
            value.observer = value._observer()
        self.rejected(stale)

    def test_provider_final_and_turn_answer_are_exact_bytes(self) -> None:
        def answer(value: Scenario) -> None:
            completed = [item for item in value.controller if item.get("type") == "turn_complete"][-1]
            completed["answer"] = str(completed["answer"]) + " changed"
            value.observer = value._observer()
        self.rejected(answer)
        def proof(value: Scenario) -> None:
            final = [item for item in value.controller if item.get("type") == "model_response" and item.get("response_type") == "final"][-1]
            final["provider_proof_sha256"] = "0" * 64
            value.observer = value._observer()
        self.rejected(proof)

    def test_observer_is_complete_ordered_and_content_free(self) -> None:
        self.validate()
        def leak(value: Scenario) -> None:
            value.observer[2]["content"] = "controller secret"
        self.rejected(leak)
        def drop(value: Scenario) -> None:
            index = next(i for i, item in enumerate(value.observer) if item.get("event") == "tool_event")
            value.observer.pop(index)
        self.rejected(drop)

    def test_active_worker_cancel_is_targeted_and_next_turn_recovers(self) -> None:
        scenario = Scenario(cancel=True)
        summary = self.validate(
            scenario,
            goals=None,
            require_acceptance_scenarios=False,
        )
        self.assertTrue(summary.turns[2].cancelled_active_worker)
        self.assertEqual(summary.turns[2].status, "cancelled")
        self.assertTrue(summary.turns[3].direct_final)

    def test_child_cancel_waits_for_root_before_synthetic_settlement(self) -> None:
        scenario = Scenario(cancel=True)
        root = next(
            item
            for item in scenario.controller
            if item.get("type") == "task_event"
            and item.get("turn_id") == 3
            and item.get("parent_task_id") == 0
            and item.get("event") == "cancelled"
        )
        self.assertTrue(
            validator._root_binds_derived_cancel(root, turn_id=3, corr_id=9)
        )
        root["summary"] = "generic_failure"
        scenario.observer = scenario._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(
                scenario, goals=None, require_acceptance_scenarios=False
            )

    def test_deadline_tool_event_wins_cancel_interleaving(self) -> None:
        scenario = Scenario(cancel=True, cancel_with_tool_event=True)
        summary = self.validate(
            scenario, goals=None, require_acceptance_scenarios=False
        )
        self.assertTrue(summary.turns[2].cancelled_active_worker)
        self.assertEqual(summary.turns[2].status, "cancelled")
        events = [
            item.get("type")
            for item in scenario.controller
            if item.get("turn_id") == 3
        ]
        self.assertLess(events.index("turn_cancelling"), events.index("tool_event"))

    def test_child_cancel_may_cross_before_host_cancel(self) -> None:
        scenario = Scenario(cancel=True, cancel_after_child=True)
        summary = self.validate(
            scenario, goals=None, require_acceptance_scenarios=False
        )
        self.assertTrue(summary.turns[2].cancelled_active_worker)
        turn = [
            item for item in scenario.controller if item.get("turn_id") == 3
        ]
        child_index = next(
            index
            for index, item in enumerate(turn)
            if item.get("type") == "task_event"
            and item.get("parent_task_id") != 0
            and item.get("event") == "cancelled"
        )
        cancel_index = next(
            index for index, item in enumerate(turn)
            if item.get("type") == "turn_cancelling"
        )
        self.assertLess(child_index, cancel_index)

    def test_exact_cleanup_root_is_the_only_failed_cancel_binding(self) -> None:
        scenario = Scenario(cancel=True)
        root = next(
            item
            for item in scenario.controller
            if item.get("type") == "task_event"
            and item.get("turn_id") == 3
            and item.get("parent_task_id") == 0
            and item.get("event") == "cancelled"
        )
        root.update(
            {
                "event": "failed",
                "task_state": "failed",
                "status": ledger_module.AGENT_STATUS_IO_ERROR,
                "summary": validator.ARTIFACT_CLEANUP_SESSION_BLOCK,
            }
        )
        self.assertTrue(
            validator._root_binds_derived_cancel(root, turn_id=3, corr_id=9)
        )
        root["summary"] = "turn_failed"
        self.assertFalse(
            validator._root_binds_derived_cancel(root, turn_id=3, corr_id=9)
        )

    def test_cleanup_failure_with_tool_uses_normal_two_phase_settlement(self) -> None:
        scenario = Scenario(
            cancel=True,
            cleanup_failure=True,
            cleanup_tool_event=True,
        )
        summary = self.validate(
            scenario, goals=None, require_acceptance_scenarios=False
        )
        self.assertEqual(summary.turns[-1].status, "error")
        turn = [
            item for item in scenario.controller if item.get("turn_id") == 3
        ]
        root_index = next(
            index
            for index, item in enumerate(turn)
            if item.get("type") == "task_event"
            and item.get("parent_task_id") == 0
            and item.get("event") == "failed"
        )
        tool_index = next(
            index for index, item in enumerate(turn)
            if item.get("type") == "tool_event"
        )
        self.assertLess(root_index, tool_index)
        self.assertEqual(
            turn[tool_index]["result"], validator.ARTIFACT_CLEANUP_SESSION_BLOCK
        )

    def test_cleanup_failure_without_tool_derives_only_at_turn_error(self) -> None:
        scenario = Scenario(cancel=True, cleanup_failure=True)
        summary = self.validate(
            scenario, goals=None, require_acceptance_scenarios=False
        )
        self.assertEqual(summary.turns[-1].status, "error")
        self.assertFalse(
            any(
                item.get("type") == "tool_event" and item.get("turn_id") == 3
                for item in scenario.controller
            )
        )
        self.assertEqual(scenario.controller[-1]["reason"], "session_error")

    def test_cleanup_failure_rejects_wrong_root_marker_or_correlation(self) -> None:
        wrong_marker = Scenario(cancel=True, cleanup_failure=True)
        root = next(
            item
            for item in wrong_marker.controller
            if item.get("type") == "task_event"
            and item.get("turn_id") == 3
            and item.get("parent_task_id") == 0
            and item.get("event") == "failed"
        )
        root["summary"] = "turn_failed"
        wrong_marker.observer = wrong_marker._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(
                wrong_marker, goals=None, require_acceptance_scenarios=False
            )

        wrong_corr = Scenario(
            cancel=True,
            cleanup_failure=True,
            cleanup_tool_event=True,
        )
        root = next(
            item
            for item in wrong_corr.controller
            if item.get("type") == "task_event"
            and item.get("turn_id") == 3
            and item.get("parent_task_id") == 0
            and item.get("event") == "failed"
        )
        root["corr_id"] = int(root["corr_id"]) + 1
        wrong_corr.observer = wrong_corr._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(
                wrong_corr, goals=None, require_acceptance_scenarios=False
            )

    def test_cleanup_failure_rejects_wrong_followup_tool_marker_or_corr(self) -> None:
        for field, replacement in (
            ("result", "turn_failed"),
            ("corr_id", 10),
        ):
            with self.subTest(field=field):
                scenario = Scenario(
                    cancel=True,
                    cleanup_failure=True,
                    cleanup_tool_event=True,
                )
                tool = next(
                    item
                    for item in scenario.controller
                    if item.get("type") == "tool_event"
                    and item.get("turn_id") == 3
                )
                tool[field] = replacement
                scenario.observer = scenario._observer()
                with self.assertRaises(validator.ValidationError):
                    self.validate(
                        scenario,
                        goals=None,
                        require_acceptance_scenarios=False,
                    )

    def test_cancel_without_active_child_is_rejected(self) -> None:
        scenario = Scenario(cancel=True)
        index = next(i for i, item in enumerate(scenario.controller) if item.get("type") == "turn_cancelling")
        # Move cancellation before the worker assignment while leaving provider response intact.
        assigned = min(
            i for i in range(index) if scenario.controller[i].get("type") == "task_event"
            and scenario.controller[i].get("parent_task_id") != 0
        )
        event = scenario.controller.pop(index)
        scenario.controller.insert(assigned, event)
        scenario.observer = scenario._observer()
        with self.assertRaises(validator.ValidationError):
            self.validate(scenario, goals=None, require_acceptance_scenarios=False)

    def test_session_close_is_unique_and_last(self) -> None:
        self.rejected(lambda value: value.controller.append({"type": "session_closed", "reason": "again"}))

    def test_script_contract_is_natural_and_business_neutral(self) -> None:
        script = (
            "/tools\n" + GOALS[0] + "\n/status\n" + GOALS[1]
            + "\n/context\n/tasks\n/artifacts\n/quit\n"
        )
        self.assertEqual(validator._validate_script_text(script), GOALS)
        with self.assertRaises(validator.ValidationError):
            validator._validate_script_text(script.replace("/quit", "/deny"))
        with self.assertRaises(validator.ValidationError):
            validator._validate_script_text(script + "core=3.118x\n")

    def test_checked_in_script_and_capture_seed_are_generic_and_well_formed(self) -> None:
        goals = validator._validate_script_text(
            validator.DEFAULT_SCRIPT.read_text(encoding="utf-8")
        )
        fixture = validator._validate_fixture(
            validator._load_jsonl(
                validator.ROOT / "ci" / "agentos-nexus-replay.jsonl",
                "checked-in replay fixture",
            )
        )
        self.assertEqual(len(goals), 2)
        self.assertGreaterEqual(len(fixture), 2)
        response_types = [record.response["type"] for record in fixture]
        self.assertGreaterEqual(response_types.count("final"), 2)
        self.assertIn("tool_use", response_types)
        # A genuine provider capture may complete without a retryable error.
        # Error-record shape and retry semantics are covered by synthetic
        # mutation tests rather than manufacturing an error in this fixture.
        self.assertLessEqual(set(response_types), {"final", "tool_use", "error"})
        self.assertFalse(
            {
                response.get("tool")
                for record in fixture
                for response in [record.response]
                if response["type"] == "tool_use"
            }
            & {"tool_search", "delegate_task", "publish_report"}
        )
        self.assertTrue(all(set(record.request_sha256) != {"0"} for record in fixture))


if __name__ == "__main__":
    unittest.main()
