#!/usr/bin/env python3
"""Focused Host tests for the additive AgentOS Nexus Guest profile."""

from __future__ import annotations

import io
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_cli as cli
import agentos_console as console
import agentos_local_protocol as local
import agentos_observe as observe
import agentos_relayd as daemon
import agentos_source_attestation as source_attestation
import guest_llm_relay as relay


SESSION = "1234567890abcdef1234567890abcdef"


class NeverProvider:
    def complete(self, _request, *, deadline_monotonic=None):
        del deadline_monotonic
        raise AssertionError("provider must not be called")


class ReplyProvider:
    def __init__(self, *replies: relay.ModelReply) -> None:
        self.replies = list(replies)

    def complete(self, _request, *, deadline_monotonic=None):
        del deadline_monotonic
        return self.replies.pop(0)


class ScriptedProvider:
    def __init__(self, *outcomes: relay.ModelReply | relay.RelayError) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, _request, *, deadline_monotonic=None):
        del deadline_monotonic
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, relay.RelayError):
            raise outcome
        return outcome


class SyntheticSourceAttestor:
    """Minimal frozen corpus seam for session-state tests.

    The source-attestation module has its own binary-corpus tests.  These tests
    exercise Relay ordering and binding without rebuilding that corpus for each
    session.
    """

    def __init__(self) -> None:
        self.event: dict[str, object] | None = None
        self.search_projection = "attested discovery projection"

    def verify_evidence_event(self, event: object) -> bool:
        if not isinstance(event, dict):
            return False
        self.event = dict(event)
        return True

    def attest_read(self, _source_id: object, _start: object, _end: object):
        event = self.event
        if event is None:
            raise source_attestation.SourceAttestationError("missing synthetic event")

        class Replay:
            def evidence_binding(
                self, corr_id: object, task_id: object, provenance: object
            ) -> dict[str, object]:
                envelope = {
                    "version": 1,
                    "corr_id": corr_id,
                    "tool": "source_read",
                    "task_id": task_id,
                    "provenance": provenance,
                }
                return {
                    field: envelope.get(field, event[field])
                    for field in source_attestation._EVIDENCE_BINDING_FIELDS
                }

        return Replay()

    def attest_search(self, query: object, path_prefix: object = ""):
        if query != "symbol" or path_prefix != "os/":
            raise source_attestation.SourceAttestationError(
                "unexpected synthetic search"
            )
        expected_projection = self.search_projection

        class Search:
            projection = expected_projection
            projection_sha256 = hashlib.sha256(
                expected_projection.encode("utf-8")
            ).hexdigest()

        return Search()


def host_source_search_attestor() -> source_attestation.SourceAttestation:
    """Build a tiny immutable Host corpus for end-to-end search settlement tests."""

    content = b"int symbol(void) { return 1; }\n"
    record = source_attestation.SourceRecord(
        source_id="S0001",
        path="os/search_fixture.c",
        content=content,
        full_sha256=hashlib.sha256(content).hexdigest(),
        line_starts=(0,),
    )
    return source_attestation.SourceAttestation(
        corpus_revision="b" * 64,
        manifest_sha256="c" * 64,
        source_bytes=len(content),
        corpus_bytes=len(content),
        sources={record.source_id: record},
        volume_sha256={"nxsrc000": "d" * 64},
    )


class SessionHarness:
    def __init__(
        self,
        profile: str,
        provider=None,
        *,
        max_rounds: int | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        source_attestor=None,
    ) -> None:
        self.profile = profile
        self.lines: list[bytes] = []
        self.controller: list[dict[str, object]] = []
        self.telemetry: list[dict[str, object]] = []
        self.session = daemon.InteractiveSession(
            provider if provider is not None else NeverProvider(),
            send_line=self.lines.append,
            controller_sink=lambda value: self.controller.append(dict(value)),
            telemetry_sink=lambda value: self.telemetry.append(dict(value)),
            session_id=SESSION,
            max_rounds=max_rounds,
            max_tokens=(64 if profile == "nexus" else relay.DEFAULT_MAX_OUTPUT_TOKENS),
            guest_profile=profile,
            provider_name=provider_name,
            model_name=(
                model_name
                if model_name is not None
                else ("test-model" if profile == "nexus" else None)
            ),
            source_attestor=(
                source_attestor
                if source_attestor is not None
                else (SyntheticSourceAttestor() if profile == "nexus" else None)
            ),
        )
        kinds = (
            tuple(daemon.NEXUS_WIRE_KINDS)
            if profile == "nexus"
            else tuple(relay.WIRE_V2_KINDS)
        )
        self.codec = relay.FrameCodec(
            (
                daemon.NEXUS_MAX_PAYLOAD_BYTES
                if profile == "nexus"
                else relay.PROTOCOL_MAX_PAYLOAD_BYTES
            ),
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=kinds,
        )
        self.guest_seq = 1

    def _raw_guest(self, kind: str, payload: dict[str, object]) -> None:
        line = self.codec.encode_json(SESSION, self.guest_seq, kind, payload)
        self.guest_seq += 1
        self.session.handle_line(line)

    def _ensure_root_prelude(self, payload: dict[str, object]) -> None:
        ledger = self.session._nexus_task_ledger
        if ledger is None or ledger.snapshot().task_count:
            return
        turn_id = int(payload["turn_id"])
        corr_id = int(payload["corr_id"])
        self.session._bind_kernel_identity(
            role="coordinator", pid=5, agent_id=1, actor_control_id=0x100
        )
        common = {
            "turn_id": turn_id,
            "request_id": int(payload["request_id"]),
            "corr_id": corr_id,
            "workflow_lifecycle_id": 3,
            "workflow_lifecycle_generation": 2,
            "task_id": 100 + turn_id,
            "parent_task_id": 0,
            "role": "coordinator",
            "agent_pid": 5,
            "agent_id": 1,
            "control_id_known": True,
            "control_id": 0x100,
            "source_pid": 5,
            "target_pid": 5,
            "status": 0,
        }
        for event, state, tick in (
            ("assigned", "assigned", 90),
            ("accepted", "accepted", 91),
            ("progress", "running", 92),
        ):
            self._raw_guest(
                "TASK_EVENT", {**common, "event": event, "task_state": state, "tick": tick}
            )

    def _ensure_root_terminal(self, status: str) -> None:
        ledger = self.session._nexus_task_ledger
        if ledger is None:
            return
        snapshot = ledger.snapshot()
        roots = [task for task in snapshot.tasks if task.parent_task_id == 0]
        if not roots or roots[0].terminal_event:
            return
        event = "completed" if status == "completed" else (
            "cancelled" if status == "cancelled" else "failed"
        )
        terminal_status = 0 if event == "completed" else (-10 if event == "cancelled" else -18)
        turn = self.session.active
        assert turn is not None
        self._raw_guest(
            "TASK_EVENT",
            {
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "corr_id": self.session._active_last_corr,
                "workflow_lifecycle_id": 3,
                "workflow_lifecycle_generation": 2,
                "task_id": 100 + turn.turn_id,
                "parent_task_id": 0,
                "event": event,
                "task_state": event,
                "role": "coordinator",
                "agent_pid": 5,
                "agent_id": 1,
                "control_id_known": True,
                "control_id": 0x100,
                "source_pid": 5,
                "target_pid": 5,
                "status": terminal_status,
                "tick": 200,
            },
        )

    def guest(self, kind: str, payload: dict[str, object]) -> None:
        if self.profile == "nexus" and kind == "MODEL_REQUEST":
            self._ensure_root_prelude(payload)
        if self.profile == "nexus" and kind == "TURN_COMPLETE":
            self._ensure_root_terminal(str(payload.get("status", "completed")))
            turn = self.session.active
            assert turn is not None
            payload = {
                **payload,
                "rounds": payload.get("rounds", turn.rounds),
                "retries": payload.get("retries", turn.retries),
                "attempts": payload.get("attempts", turn.attempts),
            }
        self._raw_guest(kind, payload)

    def wait_provider(self) -> None:
        deadline = daemon.time.monotonic() + 2
        while daemon.time.monotonic() < deadline:
            if self.session.poll_provider():
                if self.profile == "nexus" and self.session._last_final_response is not None:
                    self._ensure_root_terminal("completed")
                return
            daemon.time.sleep(0.005)
        raise AssertionError("provider did not complete")


def task_event(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "workflow_lifecycle_id": 3,
        "workflow_lifecycle_generation": 2,
        "task_id": 7,
        "parent_task_id": 0,
        "event": "assigned",
        "task_state": "assigned",
        "role": "analyst",
        "agent_pid": 8,
        "agent_id": 3,
        "control_id_known": False,
        "source_pid": 8,
        "target_pid": 8,
        "status": 0,
        "tick": 100,
    }
    value.update(updates)
    return value


def model_request(
    corr_id: int,
    *,
    turn_id: int = 1,
    request_id: int = 1,
    user_content: str = "publish",
    nexus: bool = True,
) -> dict[str, object]:
    value: dict[str, object] = {
        "turn_id": turn_id,
        "request_id": request_id,
        "corr_id": corr_id,
        "model": "test-model",
        "messages": [
            {"role": "user", "content": user_content},
            {
                "role": "user",
                "content": (
                    f"{daemon.NEXUS_CONTROL_CONTEXT_PREFIX} test runtime context"
                ),
            },
        ],
        "tools": [],
        "max_tokens": 64,
    }
    if nexus:
        value.update(
            {
                "contract_version": daemon.NEXUS_AUTONOMY_CONTRACT[0],
                "policy_sha256": daemon.NEXUS_AUTONOMY_CONTRACT[1],
                "tool_catalog_sha256": daemon.NEXUS_AUTONOMY_CONTRACT[2],
                "system": daemon.NEXUS_SYSTEM_POLICY,
                "tools": json.loads(daemon.NEXUS_TOOL_CATALOG_JSON),
            }
        )
    return value


def source_read_result(
    projection: str, *, status: int = 0
) -> tuple[dict[str, object], dict[str, object]]:
    inner: dict[str, object] = {
        "status": status,
        "value0": 0,
        "value1": 7 if status == 0 else 0,
        "value2": 2 if status == 0 else 0,
        "result": "source_read" if status == 0 else "source_read_failed",
    }
    if projection:
        inner.update(
            {
                "source_evidence": projection,
                "evidence_trust": "corpus_attested",
            }
        )
    canonical = relay.canonical_json_bytes(inner).decode("utf-8")
    event = {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "tool": "source_read",
        "status": status,
        "sequence": 1,
        "value0": inner["value0"],
        "value1": inner["value1"],
        "value2": inner["value2"],
        "result": inner["result"],
        "context_seq": 2,
        "provenance": 60 if status == 0 else 0,
        "projection_sha256": (
            hashlib.sha256(projection.encode("utf-8")).hexdigest()
            if projection
            else ""
        ),
        "result_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    return inner, event


def source_search_result(projection: str) -> tuple[dict[str, object], dict[str, object]]:
    inner: dict[str, object] = {
        "status": 0,
        "value0": 0,
        "value1": 9,
        "value2": 2,
        "result": "source_evidence_ready;transient=1",
        "discovery_projection": projection,
        "evidence_trust": "unverified_discovery_hint",
    }
    canonical = relay.canonical_json_bytes(inner).decode("utf-8")
    digest = hashlib.sha256(projection.encode("utf-8")).hexdigest()
    return inner, {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "tool": "source_search",
        "status": 0,
        "sequence": 1,
        "value0": 0,
        "value1": 9,
        "value2": 2,
        "result": "source_evidence_ready;transient=1",
        "context_seq": 2,
        "provenance": 60,
        "projection_sha256": digest,
        "result_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def source_search_miss_result() -> tuple[dict[str, object], dict[str, object]]:
    inner: dict[str, object] = {
        "status": daemon.NEXUS_SOURCE_SEARCH_NOT_FOUND_STATUS,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": daemon.NEXUS_SOURCE_SEARCH_NO_MATCHES_RESULT,
    }
    return inner, {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "tool": "source_search",
        "status": inner["status"],
        "sequence": 1,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": inner["result"],
        "context_seq": 2,
        "provenance": 0,
        "projection_sha256": "",
        "result_sha256": hashlib.sha256(
            relay.canonical_json_bytes(inner)
        ).hexdigest(),
    }


def failed_source_search_result(
    status: int, result: str = "task_failed;replan_allowed=1"
) -> tuple[dict[str, object], dict[str, object]]:
    inner: dict[str, object] = {
        "status": status,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": result,
    }
    return inner, {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "tool": "source_search",
        "status": status,
        "sequence": 1,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": result,
        "context_seq": 2,
        "provenance": 0,
        "projection_sha256": "",
        "result_sha256": hashlib.sha256(
            relay.canonical_json_bytes(inner)
        ).hexdigest(),
    }


def source_evidence(projection: str, **updates: object) -> dict[str, object]:
    digest = "a" * 64
    value: dict[str, object] = {
        "version": 1,
        "turn_id": 1,
        "request_id": 1,
        "corr_id": 1,
        "task_id": 7,
        "provenance": 60,
        "event": "source_read",
        "tool": "source_read",
        "scope": "build_source_snapshot",
        "corpus_revision": "b" * 64,
        "manifest_sha256": "c" * 64,
        "source_id": "S0001",
        "path": "os/schedule.c",
        "start_line": 10,
        "end_line": 12,
        "citation": "[S0001:L10-L12]",
        "full_sha256": digest,
        "chunk_sha256": "d" * 64,
        "artifact_sha256": hashlib.sha256(
            projection.encode("utf-8")
        ).hexdigest(),
        "projection_sha256": hashlib.sha256(
            projection.encode("utf-8")
        ).hexdigest(),
    }
    value.update(updates)
    return value


def emit_successful_child_task(
    harness: SessionHarness,
    tool_event: dict[str, object],
    *,
    role: str = "research",
    pid: int = 8,
    control_id: int = 0x200,
) -> None:
    task_id = int(tool_event["value1"])
    agent_id = int(tool_event["value2"])
    corr_id = int(tool_event["corr_id"])
    harness.session._bind_kernel_identity(
        role=role, pid=pid, agent_id=agent_id, actor_control_id=control_id
    )
    common = {
        "turn_id": int(tool_event["turn_id"]),
        "request_id": int(tool_event["request_id"]),
        "corr_id": corr_id,
        "workflow_lifecycle_id": 3,
        "workflow_lifecycle_generation": 2,
        "task_id": task_id,
        "parent_task_id": 100 + int(tool_event["turn_id"]),
        "role": role,
        "agent_pid": pid,
        "agent_id": agent_id,
        "control_id_known": True,
        "control_id": control_id,
        "status": 0,
        "deadline_tick": 5000,
    }
    for event, state, tick in (
        ("assigned", "assigned", 100),
        ("accepted", "accepted", 101),
        ("progress", "running", 102),
        ("completed", "completed", 103),
    ):
        route = (
            {"source_pid": 5, "target_pid": pid}
            if event == "assigned"
            else {"source_pid": pid, "target_pid": 5}
        )
        harness._raw_guest(
            "TASK_EVENT",
            {
                **common,
                **route,
                "event": event,
                "task_state": state,
                "tick": tick,
            },
        )
    harness._raw_guest(
        "TASK_EVENT",
        {
            **common,
            "event": "artifact_published",
            "task_state": "completed",
            "tick": 104,
            "artifact_handle": int(tool_event["value0"]),
            "digest": str(tool_event["projection_sha256"]),
            "provenance": int(tool_event["provenance"]),
            "resource_used": 44,
            "source_pid": pid,
            "target_pid": 5,
            "summary": "bounded controller-only artifact summary",
        },
    )


def emit_failed_child_task(
    harness: SessionHarness,
    *,
    status: int,
    corr_id: int = 1,
    task_id: int = 7,
    summary: str = "source_search_no_matches",
) -> None:
    harness.session._bind_kernel_identity(
        role="research", pid=8, agent_id=2, actor_control_id=0x200
    )
    common = {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": corr_id,
        "workflow_lifecycle_id": 3,
        "workflow_lifecycle_generation": 2,
        "task_id": task_id,
        "parent_task_id": 101,
        "role": "research",
        "agent_pid": 8,
        "agent_id": 2,
        "control_id_known": True,
        "control_id": 0x200,
        "deadline_tick": 5000,
    }
    for event, state, event_status, tick in (
        ("assigned", "assigned", 0, 100),
        ("accepted", "accepted", 0, 101),
        ("progress", "running", 0, 102),
        ("failed", "failed", status, 103),
    ):
        harness._raw_guest(
            "TASK_EVENT",
            {
                **common,
                "event": event,
                "task_state": state,
                "status": event_status,
                "source_pid": 5 if event == "assigned" else 8,
                "target_pid": 8 if event == "assigned" else 5,
                "tick": tick,
                **({"summary": summary} if event == "failed" else {}),
            },
        )


def emit_root_failure(
    harness: SessionHarness,
    *,
    corr_id: int = 1,
    summary: str = "turn_failed",
) -> None:
    harness.guest(
        "TASK_EVENT",
        task_event(
            corr_id=corr_id,
            task_id=101,
            parent_task_id=0,
            event="failed",
            task_state="failed",
            role="coordinator",
            agent_pid=5,
            agent_id=1,
            control_id_known=True,
            control_id=0x100,
            source_pid=5,
            target_pid=5,
            status=-18,
            tick=120,
            summary=summary,
        ),
    )


def cleanup_failure_tool_event(
    *, corr_id: int = 1, result: str = "artifact_cleanup_failed;session_blocked=1"
) -> dict[str, object]:
    inner = {
        "status": -18,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": result,
    }
    return {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": corr_id,
        "tool": "source_search",
        "status": -18,
        "sequence": 1,
        "value0": 0,
        "value1": 0,
        "value2": 0,
        "result": result,
        "context_seq": 2,
        "provenance": 0,
        "projection_sha256": "",
        "result_sha256": hashlib.sha256(
            relay.canonical_json_bytes(inner)
        ).hexdigest(),
    }


def emit_cancelled_child_task(
    harness: SessionHarness,
    *,
    corr_id: int = 1,
    task_id: int = 7,
    deadline_tick: int = 5000,
) -> None:
    harness.session._bind_kernel_identity(
        role="research", pid=8, agent_id=2, actor_control_id=0x200
    )
    common = {
        "corr_id": corr_id,
        "task_id": task_id,
        "parent_task_id": 101,
        "role": "research",
        "agent_pid": 8,
        "agent_id": 2,
        "control_id_known": True,
        "control_id": 0x200,
        "deadline_tick": deadline_tick,
    }
    for event, state, status, source_pid, target_pid, tick in (
        ("assigned", "assigned", 0, 5, 8, 100),
        ("accepted", "accepted", 0, 8, 5, 101),
        ("cancelled", "cancelled", -10, 8, 5, 102),
    ):
        harness.guest(
            "TASK_EVENT",
            task_event(
                **common,
                event=event,
                task_state=state,
                status=status,
                source_pid=source_pid,
                target_pid=target_pid,
                tick=tick,
            ),
        )


class NexusProfileTests(unittest.TestCase):
    def test_publish_is_absent_from_the_nexus_product_contract(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "user"
            / "src"
            / "agentnexus_ucore.c"
        ).read_text(encoding="utf-8")
        self.assertNotIn("NEXUS_PUBLISH_REPORT_ID", source)
        self.assertNotIn('"name":"publish_report"', daemon.NEXUS_TOOL_CATALOG_JSON)
        self.assertEqual(
            [tool["name"] for tool in daemon.NEXUS_TOOL_CATALOG],
            ["source_search", "source_read", "inspect_runtime", "draft_report", "read_artifact"],
        )

    def test_agentlive_hello_is_unchanged_and_nexus_negotiates_task_events(self) -> None:
        agentlive = SessionHarness("agentlive")
        agentlive.session.start()
        hello = agentlive.codec.decode(agentlive.lines[0])
        self.assertEqual(
            hello.json_object(),
            {
                "protocol": 2,
                "max_payload": relay.PROTOCOL_MAX_PAYLOAD_BYTES,
                "max_rounds": relay.DEFAULT_MAX_ROUNDS,
                "max_tokens": relay.DEFAULT_MAX_OUTPUT_TOKENS,
            },
        )
        self.assertEqual(daemon.READY_LINE, relay.GUEST_RELAY_READY_LINE)

        nexus = SessionHarness("nexus")
        nexus.session.start()
        negotiated = nexus.codec.decode(nexus.lines[0]).json_object()
        self.assertEqual(
            negotiated,
            {
                "protocol": 2,
                "max_payload": daemon.NEXUS_MAX_PAYLOAD_BYTES,
                "max_rounds": daemon.NEXUS_MAX_ROUNDS,
                "max_retries": daemon.NEXUS_MAX_RETRIES,
                "max_tokens": nexus.session.max_tokens,
                "guest_profile": "nexus",
                "features": ["task_event_v1", "evidence_event_v1"],
                "max_user_bytes": daemon.NEXUS_MAX_USER_MESSAGE_BYTES,
                "max_final_bytes": daemon.NEXUS_MAX_FINAL_BYTES,
            },
        )
        self.assertEqual(
            daemon.NEXUS_READY_LINE,
            b"agentnexus_ucore: relay_ready=1 nexus=1",
        )
        default_nexus = daemon.InteractiveSession(
            NeverProvider(),
            send_line=lambda _line: None,
            controller_sink=lambda _value: None,
            telemetry_sink=lambda _value: None,
            guest_profile="nexus",
            model_name="test-model",
            source_attestor=SyntheticSourceAttestor(),
        )
        self.assertEqual(
            default_nexus.max_tokens, relay.NEXUS_MAX_OUTPUT_TOKENS
        )
        parsed = daemon._parser().parse_args(
            ["--provider", "replay", "--guest-profile", "nexus"]
        )
        self.assertIsNone(parsed.max_output_tokens)
        self.assertEqual(nexus.controller[-1]["guest_profile"], "nexus")
        self.assertEqual(
            nexus.controller[-1]["max_retries"], daemon.NEXUS_MAX_RETRIES
        )
        nexus_status = nexus.session.status()
        self.assertEqual(
            (nexus_status["max_rounds"], nexus_status["max_retries"]),
            (daemon.NEXUS_MAX_ROUNDS, daemon.NEXUS_MAX_RETRIES),
        )
        self.assertNotIn("max_retries", agentlive.controller[-1])
        agentlive_status = agentlive.session.status()
        self.assertNotIn("max_rounds", agentlive_status)
        self.assertNotIn("max_retries", agentlive_status)
        nexus_ready_telemetry = next(
            event
            for event in nexus.telemetry
            if event.get("event") == "session_ready"
        )
        self.assertEqual(
            nexus_ready_telemetry["max_retries"], daemon.NEXUS_MAX_RETRIES
        )
        self.assertNotIn("max_retries", agentlive.telemetry[-1])

        delegate_arguments = {
            "role": "analyst",
            "task_type": "inspect",
            "objective": "summarize",
            "input_handle": 11,
            "secondary_handle": 12,
            "extra": 13,
        }

        def model_boundary(profile: str, count: int, *, replayed: bool = False):
            arguments = (
                {
                    **{"content": "summarize"},
                    **({"title": "report"} if count >= 2 else {}),
                    **({"extra": 13} if count >= 3 else {}),
                }
                if profile == "nexus"
                else dict(list(delegate_arguments.items())[:count])
            )
            tool_name = "draft_report" if profile == "nexus" else "delegate_task"
            response = {
                "type": "tool_use",
                "tool": tool_name,
                "arguments": arguments,
            }
            provider = (
                relay.ReplayProvider([relay.ReplayRecord(response)])
                if replayed
                else ReplyProvider(
                    relay.ModelReply(
                        "tool_use", tool=tool_name, arguments=arguments
                    )
                )
            )
            boundary = SessionHarness(profile, provider)
            boundary.session.start()
            boundary.session.submit_user(f"delegate with {count} arguments")
            advertised = model_request(
                1,
                user_content=f"delegate with {count} arguments",
                nexus=profile == "nexus",
            )
            if profile != "nexus":
                advertised["tools"] = [
                    {
                        "name": "delegate_task",
                        "description": "delegate",
                        "input_schema": {"type": "object"},
                    }
                ]
            boundary.guest("MODEL_REQUEST", advertised)
            boundary.wait_provider()
            return boundary.codec.decode(boundary.lines[-1])

        for count in (1, 2):
            with self.subTest(profile="nexus", arguments=count):
                self.assertEqual(model_boundary("nexus", count).kind, "MODEL_RESPONSE")
        # Replay remains subject to the same exact advertised-catalog boundary.
        self.assertEqual(
            model_boundary("nexus", 3, replayed=True).kind,
            "MODEL_ERROR",
        )
        nexus_overflow = model_boundary("nexus", 3)
        self.assertEqual(nexus_overflow.kind, "MODEL_ERROR")
        self.assertEqual(nexus_overflow.json_object()["code"], "BAD_TOOL_ARGUMENTS")

        self.assertEqual(model_boundary("agentlive", 3).kind, "MODEL_RESPONSE")
        legacy_overflow = model_boundary("agentlive", 4)
        self.assertEqual(legacy_overflow.kind, "MODEL_ERROR")
        self.assertEqual(legacy_overflow.json_object()["code"], "BAD_TOOL_ARGUMENTS")

    def test_round_policy_is_resolved_from_guest_profile(self) -> None:
        self.assertEqual(daemon.NEXUS_MAX_RETRIES, 32)
        self.assertEqual(
            daemon.NEXUS_MAX_ROUNDS + daemon.NEXUS_MAX_RETRIES, 48
        )
        self.assertEqual(SessionHarness("agentlive").session.max_rounds, 8)
        self.assertEqual(SessionHarness("agentlive").session.max_retries, 0)
        self.assertEqual(
            SessionHarness("nexus").session.max_rounds,
            daemon.NEXUS_MAX_ROUNDS,
        )
        self.assertEqual(
            SessionHarness("nexus").session.max_retries,
            daemon.NEXUS_MAX_RETRIES,
        )
        self.assertEqual(
            SessionHarness(
                "nexus", max_rounds=daemon.NEXUS_MAX_ROUNDS
            ).session.max_rounds,
            daemon.NEXUS_MAX_ROUNDS,
        )

        with self.assertRaisesRegex(ValueError, "Guest profile policy"):
            SessionHarness("agentlive", max_rounds=9)
        with self.assertRaisesRegex(ValueError, "Guest profile policy"):
            SessionHarness("nexus", max_rounds=daemon.NEXUS_MAX_ROUNDS + 1)

        parsed = daemon._parser().parse_args(["--provider", "replay"])
        self.assertIsNone(parsed.max_rounds)

    def test_retryable_error_does_not_consume_a_nexus_decision_round(self) -> None:
        provider = ScriptedProvider(
            relay.ProviderError("PROVIDER_BUSY", "retry", retryable=True),
            relay.ModelReply("final", content="recovered"),
        )
        harness = SessionHarness("nexus", provider, max_rounds=1)
        harness.session.start()
        harness.session.submit_user("recover and finish")

        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="recover and finish")
        )
        harness.wait_provider()
        first = harness.codec.decode(harness.lines[-1])
        self.assertEqual(first.kind, "MODEL_ERROR")
        self.assertTrue(first.json_object()["retryable"])
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 1))
        self.assertEqual(harness.session.status()["round"], 0)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause, ""
        )

        harness.guest(
            "MODEL_REQUEST", model_request(2, user_content="recover and finish")
        )
        harness.wait_provider()
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (2, 1, 1))
        final_snapshot = harness.session._nexus_task_ledger.snapshot()
        self.assertTrue(final_snapshot.provider_final_frozen)
        self.assertEqual(final_snapshot.termination_cause, "")
        request_events = [
            event
            for event in harness.controller
            if event.get("type") == "model_request"
        ]
        self.assertEqual([event["round"] for event in request_events], [1, 1])
        self.assertEqual([event["attempt"] for event in request_events], [1, 2])
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "recovered",
                "rounds": 1,
                "retries": 1,
                "attempts": 2,
            },
        )
        self.assertEqual(
            (
                harness.controller[-1]["rounds"],
                harness.controller[-1]["retries"],
                harness.controller[-1]["attempts"],
            ),
            (1, 1, 2),
        )
        self.assertEqual(provider.calls, 2)

    def test_nexus_local_response_rejection_consumes_retry_not_decision(self) -> None:
        harness = SessionHarness(
            "nexus",
            ScriptedProvider(
                relay.ModelReply(
                    "final", content="x" * (daemon.NEXUS_MAX_FINAL_BYTES + 1)
                ),
                relay.ModelReply("final", content="bounded recovery"),
            ),
        )
        harness.session.start()
        harness.session.submit_user("recover from an oversized final")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="recover from an oversized final"),
        )
        harness.wait_provider()
        rejected = harness.codec.decode(harness.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        self.assertTrue(rejected.json_object()["retryable"])
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 1))
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause, ""
        )

        harness.guest(
            "MODEL_REQUEST",
            model_request(2, user_content="recover from an oversized final"),
        )
        harness.wait_provider()
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (2, 1, 1))
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "bounded recovery",
                "rounds": 1,
                "retries": 1,
                "attempts": 2,
            },
        )
        self.assertEqual(
            (
                harness.controller[-1]["rounds"],
                harness.controller[-1]["retries"],
                harness.controller[-1]["attempts"],
            ),
            (1, 1, 2),
        )

    def test_nexus_adapter_response_shape_error_is_retryable(self) -> None:
        harness = SessionHarness(
            "nexus",
            ScriptedProvider(
                relay.ProviderError(
                    "MULTIPLE_TOOL_CALLS",
                    "provider returned multiple calls",
                    retryable=False,
                ),
                relay.ModelReply("final", content="recovered from adapter shape"),
            ),
        )
        harness.session.start()
        harness.session.submit_user("recover from a malformed provider response")
        harness.guest(
            "MODEL_REQUEST",
            model_request(
                1, user_content="recover from a malformed provider response"
            ),
        )
        harness.wait_provider()
        rejected = harness.codec.decode(harness.lines[-1]).json_object()
        self.assertEqual(rejected["code"], "MULTIPLE_TOOL_CALLS")
        self.assertTrue(rejected["retryable"])
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 1))
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause, ""
        )

        harness.guest(
            "MODEL_REQUEST",
            model_request(
                2, user_content="recover from a malformed provider response"
            ),
        )
        harness.wait_provider()
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (2, 1, 1))

    def test_nexus_unexpected_adapter_exception_is_retryable_and_can_replan(
        self,
    ) -> None:
        private_detail = "secret-adapter-diagnostic"

        class CrashingThenRecoveringProvider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError(private_detail)
                return relay.ModelReply("final", content="recovered")

        provider = CrashingThenRecoveringProvider()
        harness = SessionHarness("nexus", provider, max_rounds=1)
        harness.session.start()
        harness.session.submit_user("recover from an adapter exception")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="recover from an adapter exception"),
        )
        harness.wait_provider()

        rejected = harness.codec.decode(harness.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        error = rejected.json_object()
        self.assertEqual(error["code"], "PROVIDER_ADAPTER_ERROR")
        self.assertEqual(error["message"], "provider adapter failed unexpectedly")
        self.assertTrue(error["retryable"])
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 1))
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause, ""
        )
        self.assertNotIn(private_detail, b"".join(harness.lines).decode("utf-8"))
        self.assertNotIn(
            private_detail,
            json.dumps(harness.controller, ensure_ascii=False),
        )
        self.assertNotIn(
            private_detail,
            json.dumps(harness.telemetry, ensure_ascii=False),
        )

        harness.guest(
            "MODEL_REQUEST",
            model_request(2, user_content="recover from an adapter exception"),
        )
        harness.wait_provider()
        delivered = harness.codec.decode(harness.lines[-1])
        self.assertEqual(delivered.kind, "MODEL_RESPONSE")
        self.assertEqual(delivered.json_object()["content"], "recovered")
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (2, 1, 1))
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "recovered",
                "rounds": 1,
                "retries": 1,
                "attempts": 2,
            },
        )
        self.assertEqual(provider.calls, 2)

    def test_unexpected_adapter_exception_remains_fatal_outside_nexus(self) -> None:
        private_detail = "secret-legacy-adapter-diagnostic"

        class CrashingProvider:
            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                raise RuntimeError(private_detail)

        harness = SessionHarness("agentlive", CrashingProvider())
        harness.session.start()
        harness.session.submit_user("legacy adapter failure")
        harness.guest(
            "MODEL_REQUEST",
            model_request(
                1, user_content="legacy adapter failure", nexus=False
            ),
        )
        harness.wait_provider()

        rejected = harness.codec.decode(harness.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        error = rejected.json_object()
        self.assertEqual(error["code"], "PROVIDER_FAILURE")
        self.assertEqual(error["message"], "provider adapter failed unexpectedly")
        self.assertFalse(error["retryable"])
        self.assertNotIn(private_detail, b"".join(harness.lines).decode("utf-8"))

    def test_nexus_base_exception_remains_fatal(self) -> None:
        class ProviderAbort(BaseException):
            pass

        class AbortingProvider:
            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                raise ProviderAbort("private abort detail")

        harness = SessionHarness("nexus", AbortingProvider())
        harness.session.start()
        harness.session.submit_user("adapter abort")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="adapter abort")
        )
        harness.wait_provider()

        rejected = harness.codec.decode(harness.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        error = rejected.json_object()
        self.assertEqual(error["code"], "PROVIDER_FAILURE")
        self.assertEqual(error["message"], "provider adapter failed unexpectedly")
        self.assertFalse(error["retryable"])
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 0))
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause,
            "provider_fatal",
        )

    def test_nexus_delivery_failure_does_not_consume_outcome_budget(self) -> None:
        cases = (
            (relay.ModelReply("final", content="not delivered"), "rounds"),
            (
                relay.ProviderError("PROVIDER_BUSY", "retry", retryable=True),
                "retries",
            ),
        )
        for outcome, counter in cases:
            with self.subTest(counter=counter):
                harness = SessionHarness("nexus", ScriptedProvider(outcome))
                harness.session.start()
                harness.session.submit_user("delivery fails")
                harness.guest(
                    "MODEL_REQUEST",
                    model_request(1, user_content="delivery fails"),
                )

                def fail_delivery(_line: bytes) -> None:
                    raise OSError("serial delivery failed")

                harness.session.send_line = fail_delivery
                with self.assertRaises(OSError):
                    harness.wait_provider()
                turn = harness.session.active
                self.assertIsNotNone(turn)
                assert turn is not None
                self.assertEqual(turn.attempts, 1)
                self.assertEqual(getattr(turn, counter), 0)

    def test_nexus_retry_cap_is_delivered_and_fail_closed(self) -> None:
        provider = ScriptedProvider(
            *(
                relay.ProviderError("PROVIDER_BUSY", "retry", retryable=True)
                for _ in range(daemon.NEXUS_MAX_RETRIES)
            )
        )
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        harness.session.submit_user("bounded retries")

        for corr_id in range(1, daemon.NEXUS_MAX_RETRIES + 1):
            harness.guest(
                "MODEL_REQUEST",
                model_request(corr_id, user_content="bounded retries"),
            )
            harness.wait_provider()
            frame = harness.codec.decode(harness.lines[-1])
            self.assertEqual(frame.kind, "MODEL_ERROR")
            self.assertTrue(frame.json_object()["retryable"])

        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(
            (turn.attempts, turn.rounds, turn.retries),
            (daemon.NEXUS_MAX_RETRIES, 0, daemon.NEXUS_MAX_RETRIES),
        )
        request_events = [
            event
            for event in harness.controller
            if event.get("type") == "model_request"
        ]
        self.assertEqual(
            [event["round"] for event in request_events],
            [1] * daemon.NEXUS_MAX_RETRIES,
        )
        self.assertEqual(
            [event["attempt"] for event in request_events],
            list(range(1, daemon.NEXUS_MAX_RETRIES + 1)),
        )
        self.assertLessEqual(
            turn.attempts, daemon.NEXUS_MAX_ROUNDS + daemon.NEXUS_MAX_RETRIES
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause,
            "round_limit",
        )
        with self.assertRaises(relay.WireProtocolError) as capped:
            harness.guest(
                "MODEL_REQUEST",
                model_request(
                    daemon.NEXUS_MAX_RETRIES + 1,
                    user_content="bounded retries",
                ),
            )
        self.assertEqual(capped.exception.code, "ROUND_LIMIT")
        self.assertEqual(provider.calls, daemon.NEXUS_MAX_RETRIES)

    def test_nexus_combined_decision_and_retry_attempt_cap_is_48(self) -> None:
        self.assertEqual(daemon.NEXUS_MAX_ROUNDS, 16)
        self.assertEqual(daemon.NEXUS_MAX_RETRIES, 32)
        self.assertEqual(
            daemon.NEXUS_MAX_ROUNDS + daemon.NEXUS_MAX_RETRIES, 48
        )

    def test_retry_then_last_real_tool_owns_round_limit(self) -> None:
        provider = ScriptedProvider(
            relay.ProviderError("PROVIDER_BUSY", "retry", retryable=True),
            relay.ModelReply(
                "tool_use", tool="read_artifact", arguments={"handle": 0x1234}
            ),
        )
        harness = SessionHarness("nexus", provider, max_rounds=1)
        harness.session.start()
        harness.session.submit_user("retry then read")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="retry then read")
        )
        harness.wait_provider()
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause, ""
        )

        harness.guest(
            "MODEL_REQUEST", model_request(2, user_content="retry then read")
        )
        harness.wait_provider()
        delivered = harness.codec.decode(harness.lines[-1])
        self.assertEqual(delivered.kind, "MODEL_RESPONSE")
        self.assertEqual(delivered.json_object()["type"], "tool_use")
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (2, 1, 1))
        request_events = [
            event
            for event in harness.controller
            if event.get("type") == "model_request"
        ]
        self.assertEqual([event["round"] for event in request_events], [1, 1])
        self.assertEqual([event["attempt"] for event in request_events], [1, 2])
        snapshot = harness.session._nexus_task_ledger.snapshot()
        self.assertEqual(snapshot.delivered_tool_count, 1)
        self.assertEqual(snapshot.termination_cause, "round_limit")

    def test_agentlive_retry_keeps_legacy_round_accounting(self) -> None:
        provider = ScriptedProvider(
            relay.ProviderError("PROVIDER_BUSY", "retry", retryable=True)
        )
        harness = SessionHarness("agentlive", provider, max_rounds=1)
        harness.session.start()
        harness.session.submit_user("legacy retry")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="legacy retry", nexus=False),
        )
        harness.wait_provider()
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (0, 1, 0))
        self.assertEqual(harness.session.status()["round"], 1)
        self.assertNotIn("max_retries", harness.session.status())
        with self.assertRaises(relay.WireProtocolError) as caught:
            harness.guest(
                "MODEL_REQUEST",
                model_request(2, user_content="legacy retry", nexus=False),
            )
        self.assertEqual(caught.exception.code, "ROUND_LIMIT")
        self.assertEqual(provider.calls, 1)

    def test_nexus_autonomy_contract_and_message_shape_are_host_attested(self) -> None:
        self.assertEqual(
            hashlib.sha256(daemon.NEXUS_SYSTEM_POLICY.encode("utf-8")).hexdigest(),
            daemon.NEXUS_AUTONOMY_CONTRACT[1],
        )
        self.assertEqual(
            hashlib.sha256(daemon.NEXUS_TOOL_CATALOG_JSON.encode("utf-8")).hexdigest(),
            daemon.NEXUS_AUTONOMY_CONTRACT[2],
        )

        def rejected(mutator) -> None:
            harness = SessionHarness("nexus")
            harness.session.start()
            harness.session.submit_user("inspect")
            value = model_request(1, user_content="inspect")
            mutator(value)
            with self.assertRaises(relay.WireProtocolError):
                harness.guest("MODEL_REQUEST", value)
            self.assertFalse(
                any(event.get("type") == "model_request" for event in harness.controller)
            )

        mutations = (
            lambda value: value.__setitem__("contract_version", 1),
            lambda value: value.__setitem__("policy_sha256", "0" * 64),
            lambda value: value.__setitem__("tool_catalog_sha256", "0" * 64),
            lambda value: value.__setitem__("system", daemon.NEXUS_SYSTEM_POLICY + "x"),
            lambda value: value["tools"].pop(),
            lambda value: value["tools"][0].__setitem__("description", "changed"),
            lambda value: value["tools"].reverse(),
            lambda value: value.__setitem__("tool_choice", "required"),
            lambda value: value.__setitem__("temperature", 0),
            lambda value: value.__setitem__("model", "other-model"),
            lambda value: value.__setitem__("max_tokens", 63),
            lambda value: value["messages"].insert(
                0, {"role": "assistant", "content": "preselected conclusion"}
            ),
            lambda value: value["messages"].append(
                {"role": "assistant", "content": "preselected conclusion"}
            ),
            lambda value: value["messages"].append(
                {"role": "user", "content": "hidden second goal"}
            ),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                rejected(mutation)

        accepted = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="ok"))
        )
        accepted.session.start()
        accepted.session.submit_user("inspect")
        raw = model_request(1, user_content="inspect")
        expected_raw = hashlib.sha256(
            relay.canonical_json_bytes(
                {key: value for key, value in raw.items() if key not in ("turn_id", "request_id")}
            )
        ).hexdigest()
        accepted.guest("MODEL_REQUEST", raw)
        event = next(
            event for event in accepted.controller if event.get("type") == "model_request"
        )
        self.assertEqual(event["raw_guest_request_sha256"], expected_raw)
        self.assertNotEqual(event["raw_guest_request_sha256"], event["request_sha256"])

        configured = SessionHarness(
            "nexus",
            ReplyProvider(relay.ModelReply("final", content="ok")),
            model_name="configured-model",
        )
        configured.session.start()
        configured.session.submit_user("inspect")
        no_model = model_request(1, user_content="inspect")
        no_model.pop("model")
        configured.guest("MODEL_REQUEST", no_model)
        configured.wait_provider()

    def test_deepseek_success_proof_binds_utf8_task_request_and_response(self) -> None:
        class FakeResponse:
            def __init__(self, value: object) -> None:
                self._stream = io.BytesIO(relay.canonical_json_bytes(value))
                self.status = 200
                self.headers: dict[str, str] = {}

            def read(self, count: int = -1) -> bytes:
                return self._stream.read(count)

            def getcode(self) -> int:
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                return None

        class FakeOpener:
            def __init__(self) -> None:
                self.requests: list[object] = []

            def open(self, request, timeout: float):
                del timeout
                self.requests.append(request)
                return FakeResponse(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": "provider-ok",
                                },
                            }
                        ]
                    }
                )

        secret = "sk-provider-proof-must-not-leak"
        opener = FakeOpener()
        provider = relay.DeepSeekProvider(
            relay.JsonHttpsClient(
                relay.DEEPSEEK_DEFAULT_ENDPOINT,
                opener=opener,
                secrets_to_redact=(secret,),
            ),
            api_key=secret,
            model=relay.DEEPSEEK_DEFAULT_MODEL,
        )
        harness = SessionHarness(
            "nexus",
            provider,
            provider_name="deepseek",
            model_name=relay.DEEPSEEK_DEFAULT_MODEL,
        )
        harness.session.start()
        user_content = "检查 AgentOS 调度"
        raw_user = user_content.encode("utf-8")
        expected_user_sha256 = hashlib.sha256(raw_user).hexdigest()
        harness.session.submit_user(user_content)
        request = model_request(1, user_content=user_content)
        request["model"] = relay.DEEPSEEK_DEFAULT_MODEL
        harness.guest("MODEL_REQUEST", request)
        harness.wait_provider()

        start = next(
            event for event in harness.controller if event.get("type") == "turn_started"
        )
        request_event = next(
            event for event in harness.controller if event.get("type") == "model_request"
        )
        response_event = next(
            event for event in harness.controller if event.get("type") == "model_response"
        )
        observer_event = next(
            event
            for event in harness.telemetry
            if event.get("event") == "provider_result"
        )
        delivered = harness.codec.decode(harness.lines[-1]).json_object()

        for event in (start, request_event, response_event, observer_event):
            self.assertEqual(event["user_content_sha256"], expected_user_sha256)
            self.assertEqual(event["user_bytes"], len(raw_user))
            self.assertEqual(event["generation"], start["generation"])
        self.assertEqual(response_event["provider"], "deepseek")
        self.assertEqual(response_event["model"], relay.DEEPSEEK_DEFAULT_MODEL)
        self.assertEqual(response_event["transport"], "https")
        self.assertIs(response_event["adapter_success"], True)
        self.assertEqual(
            response_event["endpoint_origin"], "https://api.deepseek.com"
        )
        self.assertEqual(
            response_event["request_sha256"], request_event["request_sha256"]
        )
        self.assertEqual(
            response_event["response_sha256"],
            hashlib.sha256(relay.canonical_json_bytes(delivered)).hexdigest(),
        )
        for key in (
            "generation",
            "provider",
            "model",
            "transport",
            "adapter_success",
            "request_sha256",
            "history_bindings",
            "response_sha256",
            "user_content_sha256",
            "user_bytes",
            "endpoint_origin",
        ):
            self.assertEqual(observer_event[key], response_event[key])
        observer_wire = json.dumps(observer_event, ensure_ascii=False).lower()
        self.assertEqual(
            set(observer_event),
            {
                "type",
                "event",
                "source",
                "turn_id",
                "request_id",
                "corr_id",
                "status",
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
                "endpoint_origin",
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
                "final_request_sha256",
                "final_evidence_root",
                "final_response_sha256",
                "provider_proof_sha256",
            },
        )
        self.assertEqual(response_event["http_status"], 200)
        self.assertEqual(response_event["provider_endpoint"], relay.DEEPSEEK_DEFAULT_ENDPOINT)
        self.assertEqual(response_event["tool_choice_mode"], "auto")
        self.assertEqual(response_event["raw_tool_call_count"], 0)
        self.assertIsNone(response_event["forced_tool"])
        self.assertEqual(response_event["selected_tool_sha256"], "")
        self.assertEqual(
            response_event["final_request_sha256"], response_event["request_sha256"]
        )
        self.assertRegex(str(response_event["provider_request_sha256"]), r"^[0-9a-f]{64}$")
        self.assertRegex(str(response_event["provider_response_sha256"]), r"^[0-9a-f]{64}$")
        self.assertNotIn(secret.lower(), observer_wire)
        self.assertNotIn(user_content.lower(), observer_wire)
        self.assertNotIn("provider-ok", observer_wire)
        self.assertNotIn("authorization", observer_wire)
        self.assertEqual(len(opener.requests), 1)

    def test_model_request_hashes_the_latest_four_tool_projections(self) -> None:
        contents = ("alpha", "证据二", "gamma", "delta", "epsilon")

        def proof(contents_for_request: tuple[str, ...]) -> list[str]:
            messages: list[dict[str, object]] = []
            for index, content in enumerate(contents_for_request, 1):
                messages.extend(
                    (
                        {
                            "role": "assistant",
                            "tool_use": {
                                "corr_id": index,
                                "tool": "source_search",
                                "arguments": {"query": "symbol"},
                            },
                        },
                        {
                            "role": "tool",
                            "tool_corr_id": index,
                            "content": relay.canonical_json_bytes(
                                {"discovery_projection": content}
                            ).decode("utf-8"),
                            "is_error": False,
                        },
                    )
                )
            records = daemon._history_records({"messages": messages})
            return [
                str(binding["projection_sha256"])
                for binding in daemon._history_bindings(records)
            ]

        self.assertEqual(proof(()), [])
        self.assertEqual(
            proof(contents[:2]),
            [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in contents[:2]],
        )
        self.assertEqual(
            proof(contents[:4]),
            [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in contents[:4]],
        )
        self.assertEqual(
            proof(contents),
            [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in contents[-4:]],
        )

        report = "model-authored report"
        records = daemon._history_records(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_use": {
                            "corr_id": 7,
                            "tool": "draft_report",
                            "arguments": {"content": report},
                        },
                    },
                    {
                        "role": "tool",
                        "tool_corr_id": 7,
                        "content": relay.canonical_json_bytes(
                            {
                                "status": 0,
                                "value0": 65538,
                                "value1": 1000,
                                "value2": 4,
                                "result": "report_drafted;handle=65538",
                                "model_authored_content": report,
                                "integrity_verified": True,
                                "content_trust": "untrusted_model_derived",
                            }
                        ).decode("utf-8"),
                        "is_error": False,
                    },
                ]
            }
        )
        self.assertEqual(
            daemon._history_bindings(records),
            (
                {
                    "tool_corr_id": 7,
                    "tool": "draft_report",
                    "projection_sha256": hashlib.sha256(
                        report.encode("utf-8")
                    ).hexdigest(),
                },
            ),
        )

    def test_source_evidence_commits_only_with_matching_tool_and_binds_final(self) -> None:
        tool_arguments = {"source_id": "S0001", "start_line": 10, "max_lines": 3}
        provider = ReplyProvider(
            relay.ModelReply(
                "tool_use", tool="source_read", arguments=tool_arguments
            ),
            relay.ModelReply("final", content="grounded answer"),
        )
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        goal = "read the scheduler evidence"
        harness.session.submit_user(goal)
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content=goal)
        )
        harness.wait_provider()

        projection = (
            "scope=build_source_snapshot\nrevision=" + "b" * 64
            + "\nmanifest_sha256=" + "c" * 64
            + "\nsource_id=S0001\npath=os/schedule.c\nstart_line=10"
            + "\nend_line=12\ncitation=[S0001:L10-L12]\nfull_sha256="
            + "a" * 64 + "\nchunk_sha256=" + "d" * 64
            + "\ncontent_untrusted=1\n"
        )
        inner, tool = source_read_result(projection)
        harness.guest("EVIDENCE_EVENT", source_evidence(projection))
        self.assertFalse(
            any(event.get("type") == "evidence_event" for event in harness.controller)
        )
        emit_successful_child_task(harness, tool)
        harness.guest("TOOL_EVENT", tool)
        public_types = [
            event.get("type")
            for event in harness.controller
            if event.get("type") in ("evidence_event", "tool_event")
        ]
        self.assertEqual(public_types, ["evidence_event", "tool_event"])
        evidence_public = next(
            event for event in harness.controller
            if event.get("type") == "evidence_event"
        )
        self.assertNotIn("source_evidence", evidence_public)

        followup = model_request(2, user_content=goal)
        followup["messages"].extend(
            (
                {
                    "role": "assistant",
                    "tool_use": {
                        "corr_id": 1,
                        "tool": "source_read",
                        "arguments": tool_arguments,
                    },
                },
                {
                    "role": "tool",
                    "tool_corr_id": 1,
                    "content": relay.canonical_json_bytes(inner).decode("utf-8"),
                    "is_error": False,
                },
            )
        )
        harness.guest("MODEL_REQUEST", followup)
        request_event = [
            event for event in harness.controller
            if event.get("type") == "model_request"
        ][-1]
        expected_projection = hashlib.sha256(
            projection.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            request_event["history_bindings"],
            [
                {
                    "tool_corr_id": 1,
                    "tool": "source_read",
                    "projection_sha256": expected_projection,
                }
            ],
        )
        harness.wait_provider()
        with self.assertRaises(relay.WireProtocolError):
            harness.guest("EVIDENCE_EVENT", source_evidence(projection))
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "grounded answer",
            },
        )
        complete = harness.controller[-1]
        self.assertEqual(complete["type"], "turn_complete")
        for field in (
            "final_request_sha256",
            "final_response_sha256",
            "provider_proof_sha256",
            "final_evidence_root",
            "final_task_root",
            "final_artifact_root",
            "final_proof_root",
        ):
            self.assertRegex(str(complete[field]), r"^[0-9a-f]{64}$")
        proof = {
            field: complete[field] for field in daemon.NEXUS_FINAL_PROOF_FIELDS
        }
        expected_root = hashlib.sha256(
            relay.canonical_json_bytes(proof)
        ).hexdigest()
        self.assertEqual(complete["final_proof_root"], expected_root)
        for field in daemon.NEXUS_FINAL_PROOF_FIELDS:
            mutated = dict(proof)
            value = mutated[field]
            mutated[field] = (
                value + 1
                if isinstance(value, int)
                else ("1" if str(value).startswith("0") else "0")
                + str(value)[1:]
            )
            self.assertNotEqual(
                hashlib.sha256(relay.canonical_json_bytes(mutated)).hexdigest(),
                expected_root,
            )

    def test_source_evidence_is_discarded_when_tool_fails(self) -> None:
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_read",
                    arguments={"source_id": "S0001", "start_line": 10, "max_lines": 3},
                )
            ),
        )
        harness.session.start()
        harness.session.submit_user("read")
        harness.guest("MODEL_REQUEST", model_request(1, user_content="read"))
        harness.wait_provider()
        projection = "uncommitted evidence"
        harness.guest("EVIDENCE_EVENT", source_evidence(projection))
        _inner, failed = source_read_result("", status=-2)
        harness.guest("TOOL_EVENT", failed)
        self.assertFalse(
            any(event.get("type") == "evidence_event" for event in harness.controller)
        )
        self.assertFalse(harness.session._staged_evidence)

    def test_no_child_cleanup_failure_latches_session_before_tool_settlement(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        def pending() -> SessionHarness:
            attestor = SyntheticSourceAttestor()
            harness = SessionHarness(
                "nexus",
                ReplyProvider(
                    relay.ModelReply(
                        "tool_use",
                        tool="source_search",
                        arguments={"query": "symbol", "path_prefix": "os/"},
                    )
                ),
                source_attestor=attestor,
            )
            harness.session.start()
            harness.session.submit_user("search")
            harness.guest("MODEL_REQUEST", model_request(1, user_content="search"))
            harness.wait_provider()
            harness.guest(
                "TASK_EVENT",
                {
                    **task_event(
                        corr_id=1,
                        task_id=101,
                        parent_task_id=0,
                        event="failed",
                        task_state="failed",
                        role="coordinator",
                        agent_pid=5,
                        agent_id=1,
                        control_id_known=True,
                        control_id=0x100,
                        source_pid=5,
                        target_pid=5,
                        status=-18,
                        tick=120,
                        summary=marker,
                    )
                },
            )
            self.assertTrue(
                harness.session._nexus_task_ledger.snapshot().session_blocked
            )
            return harness

        def failure(result: str) -> dict[str, object]:
            inner = {
                "status": -18,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
            }
            return {
                "turn_id": 1,
                "request_id": 1,
                "corr_id": 1,
                "tool": "source_search",
                "status": -18,
                "sequence": 1,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": result,
                "context_seq": 2,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": hashlib.sha256(
                    relay.canonical_json_bytes(inner)
                ).hexdigest(),
            }

        valid = pending()
        valid.guest("TOOL_EVENT", failure(marker))
        valid.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "error"},
        )
        with self.assertRaises(relay.WireProtocolError):
            valid.session.submit_user("must remain blocked")
        valid.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertTrue(valid.session.closed)
        self.assertEqual(valid.controller[-1]["reason"], "session_error")

        wrong = pending()
        with self.assertRaises(relay.WireProtocolError):
            wrong.guest("TOOL_EVENT", failure("task_failed;replan_allowed=1"))

        unstaged = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=SyntheticSourceAttestor(),
        )
        unstaged.session.start()
        unstaged.session.submit_user("search")
        unstaged.guest("MODEL_REQUEST", model_request(1, user_content="search"))
        unstaged.wait_provider()
        with self.assertRaises(relay.WireProtocolError):
            unstaged.guest("TOOL_EVENT", failure(marker))

        ordinary = SessionHarness("nexus")
        ordinary.session.start()
        with self.assertRaises(relay.WireProtocolError) as unsolicited:
            ordinary.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertEqual(unsolicited.exception.code, "BAD_CLOSE")

    def test_completed_child_cleanup_failure_preserves_authenticated_artifact(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        attestor = SyntheticSourceAttestor()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("search")
        harness.guest("MODEL_REQUEST", model_request(1, user_content="search"))
        harness.wait_provider()
        _inner, successful_tool = source_search_result(attestor.search_projection)
        emit_successful_child_task(harness, successful_tool)

        harness.guest(
            "TASK_EVENT",
            task_event(
                corr_id=1,
                task_id=101,
                parent_task_id=0,
                event="failed",
                task_state="failed",
                role="coordinator",
                agent_pid=5,
                agent_id=1,
                control_id_known=True,
                control_id=0x100,
                source_pid=5,
                target_pid=5,
                status=-18,
                tick=120,
                summary=marker,
            ),
        )
        staged = harness.session._nexus_task_ledger.snapshot()
        self.assertTrue(staged.session_blocked)
        self.assertEqual(staged.termination_cause, "session_error")
        child = next(task for task in staged.tasks if task.parent_task_id != 0)
        self.assertEqual(child.terminal_event, "completed")
        self.assertEqual(
            child.artifact_sha256,
            successful_tool["projection_sha256"],
        )

        failure_body = {
            "status": -18,
            "value0": 0,
            "value1": 0,
            "value2": 0,
            "result": marker,
        }
        harness.guest(
            "TOOL_EVENT",
            {
                "turn_id": 1,
                "request_id": 1,
                "corr_id": 1,
                "tool": "source_search",
                "status": -18,
                "sequence": 1,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": marker,
                "context_seq": 2,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": hashlib.sha256(
                    relay.canonical_json_bytes(failure_body)
                ).hexdigest(),
            },
        )
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "error"},
        )
        self.assertRegex(
            str(harness.controller[-1]["final_task_root"]), r"^[0-9a-f]{64}$"
        )
        self.assertRegex(
            str(harness.controller[-1]["final_artifact_root"]), r"^[0-9a-f]{64}$"
        )
        self.assertTrue(harness.session._nexus_task_ledger.snapshot().session_blocked)
        with self.assertRaises(relay.WireProtocolError):
            harness.session.submit_user("must remain blocked")

    def test_final_cleanup_failure_replaces_frozen_completion_with_session_error(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        harness = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="not durable"))
        )
        harness.session.start()
        harness.session.submit_user("finish")
        harness.guest("MODEL_REQUEST", model_request(1, user_content="finish"))
        deadline = daemon.time.monotonic() + 2
        while daemon.time.monotonic() < deadline:
            if harness.session.poll_provider():
                break
            daemon.time.sleep(0.005)
        else:
            self.fail("provider did not complete")
        self.assertTrue(harness.session._nexus_final_frozen)
        harness.guest(
            "TASK_EVENT",
            task_event(
                corr_id=1,
                task_id=101,
                parent_task_id=0,
                event="failed",
                task_state="failed",
                role="coordinator",
                agent_pid=5,
                agent_id=1,
                control_id_known=True,
                control_id=0x100,
                source_pid=5,
                target_pid=5,
                status=-18,
                tick=120,
                summary=marker,
            ),
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause,
            "session_error",
        )
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "error"},
        )
        self.assertNotIn("answer", harness.controller[-1])
        self.assertTrue(harness.session._nexus_task_ledger.snapshot().session_blocked)
        harness.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertEqual(harness.controller[-1]["reason"], "session_error")

    def test_cleanup_failure_upgrades_fatal_cancel_and_round_limit_outcomes(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        class FatalProvider:
            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                raise relay.ProviderError(
                    "PROVIDER_FAILURE", "fatal provider failure", retryable=False
                )

        fatal = SessionHarness("nexus", FatalProvider())
        fatal.session.start()
        fatal.session.submit_user("fatal")
        fatal.guest("MODEL_REQUEST", model_request(1, user_content="fatal"))
        fatal.wait_provider()
        fatal_turn = fatal.session.active
        self.assertIsNotNone(fatal_turn)
        assert fatal_turn is not None
        self.assertEqual(
            (fatal_turn.attempts, fatal_turn.rounds, fatal_turn.retries), (1, 0, 0)
        )
        self.assertEqual(
            fatal.session._nexus_task_ledger.snapshot().termination_cause,
            "provider_fatal",
        )
        emit_root_failure(fatal, summary=marker)
        self.assertEqual(
            fatal.session._nexus_task_ledger.snapshot().termination_cause,
            "session_error",
        )
        fatal.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        fatal.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertEqual(fatal.controller[-1]["reason"], "session_error")

        class BlockingProvider:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                self.started.set()
                self.release.wait(2)
                return relay.ModelReply("final", content="late")

        provider = BlockingProvider()
        cancelled = SessionHarness("nexus", provider)
        cancelled.session.start()
        cancelled.session.submit_user("cancel while waiting")
        cancelled.guest(
            "MODEL_REQUEST", model_request(1, user_content="cancel while waiting")
        )
        self.assertTrue(provider.started.wait(1))
        self.assertTrue(cancelled.session.cancel())
        emit_root_failure(cancelled, summary=marker)
        self.assertEqual(
            cancelled.session._nexus_task_ledger.snapshot().termination_cause,
            "session_error",
        )
        cancelled.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        cancelled.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        provider.release.set()

        round_limit = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use", tool="read_artifact", arguments={"handle": 0x1234}
                )
            ),
            max_rounds=1,
        )
        round_limit.session.start()
        round_limit.session.submit_user("read once")
        round_limit.guest(
            "MODEL_REQUEST", model_request(1, user_content="read once")
        )
        round_limit.wait_provider()
        self.assertEqual(
            round_limit.session._nexus_task_ledger.snapshot().termination_cause,
            "round_limit",
        )
        inner = {
            "status": -2,
            "value0": 0,
            "value1_omitted": "volatile_payload_size",
            "value2": 0,
            "result": "stale_report_handle",
        }
        round_limit.guest(
            "TOOL_EVENT",
            {
                "turn_id": 1,
                "request_id": 1,
                "corr_id": 1,
                "tool": "read_artifact",
                "status": -2,
                "sequence": 1,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": "stale_report_handle",
                "context_seq": 2,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": hashlib.sha256(
                    relay.canonical_json_bytes(inner)
                ).hexdigest(),
            },
        )
        emit_root_failure(round_limit, summary=marker)
        self.assertEqual(
            round_limit.session._nexus_task_ledger.snapshot().termination_cause,
            "session_error",
        )
        round_limit.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        round_limit.guest("SESSION_CLOSED", {"reason": "guest_complete"})

    def test_active_worker_cancel_cleanup_failure_needs_no_tool_event(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
        )
        harness.session.start()
        harness.session.submit_user("search then cancel")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="search then cancel")
        )
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        emit_cancelled_child_task(harness)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        emit_root_failure(harness, summary=marker)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.cancelled_cleanup_pending_corr, 1
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().termination_cause,
            "session_error",
        )
        harness.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        harness.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertFalse(
            any(
                event.get("type") == "tool_event"
                for event in harness.controller
            )
        )

    def test_cleanup_failure_root_allows_following_real_tool_event(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=SyntheticSourceAttestor(),
        )
        harness.session.start()
        harness.session.submit_user("deadline cleanup races local cancel")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="deadline cleanup races local cancel"),
        )
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        emit_cancelled_child_task(harness)
        emit_root_failure(harness, summary=marker)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.cancelled_cleanup_pending_corr, 1
        )

        harness.guest("TOOL_EVENT", cleanup_failure_tool_event())
        staged = harness.session._nexus_task_ledger.snapshot()
        self.assertEqual(staged.settled_tool_count, 1)
        self.assertEqual(staged.termination_cause, "session_error")
        self.assertEqual(
            harness.session._nexus_task_ledger.cancelled_cleanup_pending_corr, 0
        )
        harness.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        self.assertTrue(
            any(event.get("type") == "tool_event" for event in harness.controller)
        )

    def test_cancel_cleanup_wrong_root_marker_or_corr_never_synthesizes(self) -> None:
        marker = "artifact_cleanup_failed;session_blocked=1"

        def pending() -> SessionHarness:
            harness = SessionHarness(
                "nexus",
                ReplyProvider(
                    relay.ModelReply(
                        "tool_use",
                        tool="source_search",
                        arguments={"query": "symbol", "path_prefix": "os/"},
                    )
                ),
                source_attestor=SyntheticSourceAttestor(),
            )
            harness.session.start()
            harness.session.submit_user("reject malformed cleanup")
            harness.guest(
                "MODEL_REQUEST",
                model_request(1, user_content="reject malformed cleanup"),
            )
            harness.wait_provider()
            self.assertTrue(harness.session.cancel())
            emit_cancelled_child_task(harness)
            return harness

        wrong_marker = pending()
        with self.assertRaises(relay.WireProtocolError):
            emit_root_failure(wrong_marker, summary="turn_failed")
        self.assertEqual(
            wrong_marker.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        self.assertEqual(
            wrong_marker.session._nexus_task_ledger.cancelled_cleanup_pending_corr, 0
        )

        wrong_corr = pending()
        with self.assertRaises(relay.WireProtocolError):
            emit_root_failure(wrong_corr, corr_id=2, summary=marker)
        self.assertEqual(
            wrong_corr.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        self.assertEqual(
            wrong_corr.session._nexus_task_ledger.cancelled_cleanup_pending_corr, 0
        )

    def test_user_cancel_child_settles_only_at_matching_root_without_tool_event(self) -> None:
        attestor = SyntheticSourceAttestor()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("cancel active search")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="cancel active search")
        )
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        emit_cancelled_child_task(harness)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )

        wrong_corr = task_event(
            corr_id=2,
            task_id=101,
            parent_task_id=0,
            event="cancelled",
            task_state="cancelled",
            role="coordinator",
            agent_pid=5,
            agent_id=1,
            control_id_known=True,
            control_id=0x100,
            source_pid=5,
            target_pid=5,
            status=-10,
            tick=120,
            summary="turn_cancelled",
        )
        self.assertFalse(
            harness.session._nexus_user_cancel_root_needs_synthetic_tool_settlement(
                wrong_corr
            )
        )
        harness.guest(
            "TASK_EVENT",
            task_event(
                corr_id=1,
                task_id=101,
                parent_task_id=0,
                event="cancelled",
                task_state="cancelled",
                role="coordinator",
                agent_pid=5,
                agent_id=1,
                control_id_known=True,
                control_id=0x100,
                source_pid=5,
                target_pid=5,
                status=-10,
                tick=120,
                summary="turn_cancelled",
            ),
        )
        snapshot = harness.session._nexus_task_ledger.snapshot()
        self.assertEqual(snapshot.settled_tool_count, 1)
        self.assertEqual(snapshot.tasks[0].terminal_event, "cancelled")

    def test_deadline_child_cancel_tool_event_does_not_synthetic_settle(self) -> None:
        attestor = SyntheticSourceAttestor()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("deadline races cancel")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="deadline races cancel")
        )
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        emit_cancelled_child_task(harness)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )
        _inner, event = source_search_result(attestor.search_projection)
        event.update(
            {
                "status": -7,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": "task_failed;reason=deadline;replan_allowed=1",
                "provenance": 0,
                "projection_sha256": "",
            }
        )
        result = {
            "status": -7,
            "value0": 0,
            "value1_omitted": "volatile_payload_size",
            "value2": 0,
            "result": "task_failed;reason=deadline;replan_allowed=1",
        }
        event["result_sha256"] = hashlib.sha256(
            relay.canonical_json_bytes(result)
        ).hexdigest()
        harness.guest("TOOL_EVENT", event)
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 1
        )
        harness.guest(
            "TASK_EVENT",
            task_event(
                corr_id=1,
                task_id=101,
                parent_task_id=0,
                event="cancelled",
                task_state="cancelled",
                role="coordinator",
                agent_pid=5,
                agent_id=1,
                control_id_known=True,
                control_id=0x100,
                source_pid=5,
                target_pid=5,
                status=-10,
                tick=120,
                summary="turn_cancelled",
            ),
        )

    def test_ordinary_child_cancel_does_not_synthetic_settle(self) -> None:
        attestor = SyntheticSourceAttestor()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("ordinary internal cancel")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="ordinary internal cancel")
        )
        harness.wait_provider()
        emit_cancelled_child_task(harness)
        root = task_event(
            corr_id=1,
            task_id=101,
            parent_task_id=0,
            event="cancelled",
            task_state="cancelled",
            role="coordinator",
            agent_pid=5,
            agent_id=1,
            control_id_known=True,
            control_id=0x100,
            source_pid=5,
            target_pid=5,
            status=-10,
            tick=120,
            summary="turn_cancelled",
        )
        self.assertFalse(
            harness.session._nexus_user_cancel_root_needs_synthetic_tool_settlement(root)
        )
        self.assertEqual(
            harness.session._nexus_task_ledger.snapshot().settled_tool_count, 0
        )

    def test_generic_root_failure_does_not_authorize_blocked_guest_close(self) -> None:
        class FatalProvider:
            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                raise relay.ProviderError(
                    "PROVIDER_FAILURE", "fatal provider failure", retryable=False
                )

        harness = SessionHarness("nexus", FatalProvider())
        harness.session.start()
        harness.session.submit_user("fatal")
        harness.guest("MODEL_REQUEST", model_request(1, user_content="fatal"))
        harness.wait_provider()
        emit_root_failure(harness, summary="turn_failed")
        harness.guest(
            "TURN_COMPLETE", {"turn_id": 1, "request_id": 1, "status": "error"}
        )
        self.assertFalse(harness.session._nexus_task_ledger.snapshot().session_blocked)
        with self.assertRaises(relay.WireProtocolError) as unsolicited:
            harness.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertEqual(unsolicited.exception.code, "BAD_CLOSE")

    def test_source_evidence_binds_delivered_arguments_and_tool_task(self) -> None:
        arguments = {"source_id": "S0001", "start_line": 10, "max_lines": 3}

        def pending() -> tuple[SessionHarness, str]:
            harness = SessionHarness(
                "nexus",
                ReplyProvider(
                    relay.ModelReply(
                        "tool_use", tool="source_read", arguments=arguments
                    )
                ),
            )
            harness.session.start()
            harness.session.submit_user("read")
            harness.guest("MODEL_REQUEST", model_request(1, user_content="read"))
            harness.wait_provider()
            return harness, "bound source projection"

        mutations = (
            {"source_id": "S0002"},
            {"start_line": 11, "end_line": 12, "citation": "[S0001:L11-L12]"},
            {"end_line": 13, "citation": "[S0001:L10-L13]"},
            {"artifact_sha256": "0" * 64},
        )
        for update in mutations:
            with self.subTest(update=update):
                harness, projection = pending()
                evidence = source_evidence(projection)
                evidence.update(update)
                with self.assertRaises(relay.WireProtocolError):
                    harness.guest("EVIDENCE_EVENT", evidence)

        harness, projection = pending()
        harness.guest("EVIDENCE_EVENT", source_evidence(projection))
        _inner, tool = source_read_result(projection)
        emit_successful_child_task(harness, tool)
        tool["value1"] = 8
        with self.assertRaises(relay.WireProtocolError):
            harness.guest("TOOL_EVENT", tool)

    def test_source_search_semantic_outcomes_are_replayed_by_the_host(self) -> None:
        attestor = host_source_search_attestor()

        def pending(query: str) -> SessionHarness:
            arguments = {"query": query, "path_prefix": "os/"}
            harness = SessionHarness(
                "nexus",
                ReplyProvider(
                    relay.ModelReply(
                        "tool_use", tool="source_search", arguments=arguments
                    )
                ),
                source_attestor=attestor,
            )
            harness.session.start()
            harness.session.submit_user("search")
            harness.guest("MODEL_REQUEST", model_request(1, user_content="search"))
            harness.wait_provider()
            return harness

        success = pending("symbol")
        replay = attestor.attest_search("symbol", "os/")
        _inner, event = source_search_result(replay.projection)
        emit_successful_child_task(success, event)
        success.guest("TOOL_EVENT", event)
        self.assertTrue(success.session._nexus_tool_ledger[1]["discovery_attested"])

        wrong_success_result = pending("symbol")
        _inner, event = source_search_result(replay.projection)
        event["result"] = daemon.NEXUS_SOURCE_SEARCH_NO_MATCHES_RESULT
        contradictory_wrapper = {
            "status": 0,
            "value0": event["value0"],
            "value1": event["value1"],
            "value2": event["value2"],
            "result": event["result"],
            "discovery_projection": replay.projection,
            "evidence_trust": "unverified_discovery_hint",
        }
        event["result_sha256"] = hashlib.sha256(
            relay.canonical_json_bytes(contradictory_wrapper)
        ).hexdigest()
        emit_successful_child_task(wrong_success_result, event)
        with self.assertRaises(relay.WireProtocolError):
            wrong_success_result.guest("TOOL_EVENT", event)

        forged = pending("symbol")
        _inner, event = source_search_result(replay.projection + " forged")
        emit_successful_child_task(forged, event)
        with self.assertRaises(relay.WireProtocolError):
            forged.guest("TOOL_EVENT", event)

        false_miss = pending("symbol")
        _inner, event = source_search_miss_result()
        emit_failed_child_task(
            false_miss,
            status=daemon.NEXUS_SOURCE_SEARCH_NOT_FOUND_STATUS,
        )
        with self.assertRaises(relay.WireProtocolError):
            false_miss.guest("TOOL_EVENT", event)

        miss = pending("missing_symbol")
        _inner, event = source_search_miss_result()
        emit_failed_child_task(
            miss,
            status=daemon.NEXUS_SOURCE_SEARCH_NOT_FOUND_STATUS,
        )
        miss.guest("TOOL_EVENT", event)
        miss_entry = miss.session._nexus_tool_ledger[1]
        self.assertTrue(miss_entry["settled"])
        self.assertNotIn("discovery_attested", miss_entry)

        false_success = pending("missing_symbol")
        _inner, event = source_search_result("forged nonempty discovery")
        emit_successful_child_task(false_success, event)
        with self.assertRaises(relay.WireProtocolError):
            false_success.guest("TOOL_EVENT", event)

    def test_source_search_typed_miss_rejects_every_binding_mutation(self) -> None:
        attestor = host_source_search_attestor()

        def pending() -> tuple[SessionHarness, dict[str, object]]:
            harness = SessionHarness(
                "nexus",
                ReplyProvider(
                    relay.ModelReply(
                        "tool_use",
                        tool="source_search",
                        arguments={
                            "query": "missing_symbol",
                            "path_prefix": "os/",
                        },
                    )
                ),
                source_attestor=attestor,
            )
            harness.session.start()
            harness.session.submit_user("search for an absent symbol")
            harness.guest(
                "MODEL_REQUEST",
                model_request(1, user_content="search for an absent symbol"),
            )
            harness.wait_provider()
            emit_failed_child_task(
                harness,
                status=daemon.NEXUS_SOURCE_SEARCH_NOT_FOUND_STATUS,
            )
            return harness, source_search_miss_result()[1]

        mutations: tuple[tuple[str, object], ...] = (
            ("result", "task_failed;replan_allowed=1"),
            ("value0", 1),
            ("value1", 1),
            ("value2", 1),
            ("provenance", daemon.NEXUS_SUCCESS_PROVENANCE["source_search"]),
            ("projection_sha256", "f" * 64),
            ("result_sha256", "f" * 64),
        )
        for field, replacement in mutations:
            with self.subTest(field=field):
                harness, event = pending()
                event[field] = replacement
                with self.assertRaises(relay.WireProtocolError):
                    harness.guest("TOOL_EVENT", event)

    def test_source_search_ordinary_failure_is_not_a_semantic_miss(self) -> None:
        class RejectingSearchReplay(SyntheticSourceAttestor):
            def __init__(self) -> None:
                super().__init__()
                self.search_calls = 0

            def attest_search(self, query: object, path_prefix: object = ""):
                del query, path_prefix
                self.search_calls += 1
                raise AssertionError("ordinary failures must not replay source search")

        attestor = RejectingSearchReplay()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("search with an ordinary worker failure")
        harness.guest(
            "MODEL_REQUEST",
            model_request(
                1, user_content="search with an ordinary worker failure"
            ),
        )
        harness.wait_provider()
        emit_failed_child_task(harness, status=-18)
        _inner, event = failed_source_search_result(-18)
        harness.guest("TOOL_EVENT", event)
        self.assertEqual(attestor.search_calls, 0)
        entry = harness.session._nexus_tool_ledger[1]
        self.assertTrue(entry["settled"])
        self.assertNotIn("discovery_attested", entry)

    def test_replay_success_proof_never_claims_https(self) -> None:
        provider = relay.ReplayProvider(
            [relay.ReplayRecord({"type": "final", "content": "offline"})]
        )
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        harness.session.submit_user("replay task")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="replay task")
        )
        harness.wait_provider()
        response = next(
            event for event in harness.controller if event.get("type") == "model_response"
        )
        observer = next(
            event
            for event in harness.telemetry
            if event.get("event") == "provider_result"
        )
        for event in (response, observer):
            self.assertEqual(event["provider"], "replay")
            self.assertEqual(event["transport"], "replay")
            self.assertIs(event["adapter_success"], True)
            self.assertNotIn("endpoint_origin", event)
            self.assertNotIn("http_status", event)

    def test_task_hash_binding_rotates_across_turn_and_reset(self) -> None:
        provider = ReplyProvider(
            relay.ModelReply("final", content="first"),
            relay.ModelReply("final", content="second"),
        )
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        bindings: list[tuple[str, int, int]] = []
        for turn_id, (task, answer) in enumerate(
            (("第一轮", "first"), ("second turn", "second")), 1
        ):
            _, request_id = harness.session.submit_user(task)
            harness.guest(
                "MODEL_REQUEST",
                model_request(
                    turn_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    user_content=task,
                ),
            )
            harness.wait_provider()
            events = [
                event
                for event in harness.controller
                if event.get("turn_id") == turn_id
                and event.get("type")
                in ("turn_started", "model_request", "model_response")
            ]
            self.assertEqual(
                [event["type"] for event in events],
                ["turn_started", "model_request", "model_response"],
            )
            expected = hashlib.sha256(task.encode("utf-8")).hexdigest()
            expected_bytes = len(task.encode("utf-8"))
            generations = {event["generation"] for event in events}
            self.assertEqual(generations, {events[0]["generation"]})
            for event in events:
                self.assertEqual(event["user_content_sha256"], expected)
                self.assertEqual(event["user_bytes"], expected_bytes)
            bindings.append((expected, expected_bytes, int(events[0]["generation"])))
            harness.guest(
                "TURN_COMPLETE",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "status": "completed",
                    "answer": answer,
                },
            )
            if turn_id == 1:
                reset_request = harness.session.request_control("reset")
                harness.guest(
                    "CONTROL_RESULT",
                    {
                        "request_id": reset_request,
                        "command": "reset",
                        "status": "ok",
                    },
                )
        self.assertNotEqual(bindings[0], bindings[1])

    def test_profile_budgets_are_strict_unicode_byte_contracts(self) -> None:
        nexus = SessionHarness("nexus")
        nexus.session.start()
        boundary = "界" * 682 + "ab"
        self.assertEqual(len(boundary.encode("utf-8")), 2048)
        nexus.session.submit_user(boundary)
        sent = nexus.codec.decode(nexus.lines[-1]).json_object()
        self.assertEqual(sent["content"], boundary)

        overflow = SessionHarness("nexus")
        overflow.session.start()
        with self.assertRaises(relay.WireProtocolError):
            overflow.session.submit_user(boundary + "x")

        legacy = SessionHarness("agentlive")
        legacy.session.start()
        with self.assertRaises(relay.WireProtocolError):
            legacy.session.submit_user("界" * 81)

        with self.assertRaisesRegex(ValueError, "exactly match"):
            daemon.InteractiveSession(
                NeverProvider(),
                send_line=lambda _line: None,
                controller_sink=lambda _value: None,
                telemetry_sink=lambda _value: None,
                max_payload=relay.PROTOCOL_MAX_PAYLOAD_BYTES,
                guest_profile="nexus",
            )
        with self.assertRaisesRegex(ValueError, "compatible"):
            daemon.InteractiveSession(
                NeverProvider(),
                send_line=lambda _line: None,
                controller_sink=lambda _value: None,
                telemetry_sink=lambda _value: None,
                max_payload=daemon.NEXUS_MAX_PAYLOAD_BYTES,
                guest_profile="agentlive",
            )
        compatible_lines: list[bytes] = []
        compatible = daemon.InteractiveSession(
            NeverProvider(),
            send_line=compatible_lines.append,
            controller_sink=lambda _value: None,
            telemetry_sink=lambda _value: None,
            max_payload=3072,
            guest_profile="agentlive",
        )
        compatible.start()
        compatible_codec = relay.FrameCodec(
            3072,
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=tuple(relay.WIRE_V2_KINDS),
        )
        self.assertEqual(
            compatible_codec.decode(compatible_lines[0]).json_object()[
                "max_payload"
            ],
            3072,
        )
        with self.assertRaisesRegex(ValueError, "compatible"):
            daemon.InteractiveSession(
                NeverProvider(),
                send_line=lambda _line: None,
                controller_sink=lambda _value: None,
                telemetry_sink=lambda _value: None,
                max_payload=3071,
                guest_profile="agentlive",
            )

        nexus_token_limit = daemon.InteractiveSession(
            NeverProvider(),
            send_line=lambda _line: None,
            controller_sink=lambda _value: None,
            telemetry_sink=lambda _value: None,
            max_payload=daemon.NEXUS_MAX_PAYLOAD_BYTES,
            max_tokens=relay.NEXUS_MAX_OUTPUT_TOKENS,
            guest_profile="nexus",
            model_name="test-model",
            source_attestor=SyntheticSourceAttestor(),
        )
        self.assertEqual(
            nexus_token_limit.max_tokens, relay.NEXUS_MAX_OUTPUT_TOKENS
        )
        with self.assertRaisesRegex(ValueError, "outside Host policy"):
            daemon.InteractiveSession(
                NeverProvider(),
                send_line=lambda _line: None,
                controller_sink=lambda _value: None,
                telemetry_sink=lambda _value: None,
                max_tokens=relay.MAX_OUTPUT_TOKENS + 1,
                guest_profile="agentlive",
            )

        headroom = SessionHarness("nexus")
        headroom.session.start()
        headroom.session.submit_user("inspect")
        oversized = b"{}" + b" " * (
            daemon.NEXUS_MAX_MODEL_REQUEST_BYTES - 1
        )
        self.assertEqual(
            len(oversized), daemon.NEXUS_MAX_MODEL_REQUEST_BYTES + 1
        )
        line = headroom.codec.encode(
            relay.WireFrame(SESSION, 1, "MODEL_REQUEST", oversized)
        )
        with self.assertRaises(relay.WireProtocolError) as request_limit:
            headroom.session.handle_line(line)
        self.assertEqual(request_limit.exception.code, "FRAME_TOO_LARGE")

    def test_nexus_escaped_goal_and_tool_arguments_fit_the_guest_request_budget(self) -> None:
        natural = SessionHarness("nexus")
        natural.session.start()
        natural.session.submit_user("界" * 682 + "ab")

        for content in ('"' * 1025, "\n" * 342, "\0"):
            with self.subTest(goal=repr(content[:8])):
                rejected = SessionHarness("nexus")
                rejected.session.start()
                with self.assertRaises(relay.WireProtocolError) as caught:
                    rejected.session.submit_user(content)
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIsNone(rejected.session.active)

        cases = (
            (
                relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": '"' * 1401},
                ),
                "TOOL_ARGUMENT_BUDGET",
            ),
            (
                relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": "\n" * 467},
                ),
                "TOOL_ARGUMENT_BUDGET",
            ),
            (
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "界" * 32},
                ),
                "TOOL_ARGUMENT_BUDGET",
            ),
            (
                relay.ModelReply(
                    "tool_use",
                    tool="read_artifact",
                    arguments={"handle": 1 << 32},
                ),
                "BAD_TOOL_ARGUMENTS",
            ),
        )
        for reply, expected_code in cases:
            with self.subTest(tool=reply.tool, code=expected_code):
                harness = SessionHarness(
                    "nexus",
                    ReplyProvider(reply, relay.ModelReply("final", content="replanned")),
                )
                harness.session.start()
                harness.session.submit_user("bounded task")
                harness.guest(
                    "MODEL_REQUEST", model_request(1, user_content="bounded task")
                )
                harness.wait_provider()
                first = harness.codec.decode(harness.lines[-1])
                self.assertEqual(first.kind, "MODEL_ERROR")
                self.assertEqual(first.json_object()["code"], expected_code)
                self.assertTrue(first.json_object()["retryable"])
                self.assertFalse(harness.session._nexus_tool_ledger)

                harness.guest(
                    "MODEL_REQUEST", model_request(2, user_content="bounded task")
                )
                harness.wait_provider()
                final = harness.codec.decode(harness.lines[-1])
                self.assertEqual(final.kind, "MODEL_RESPONSE")
                self.assertEqual(final.json_object()["content"], "replanned")
                harness.guest(
                    "TURN_COMPLETE",
                    {
                        "turn_id": 1,
                        "request_id": 1,
                        "status": "completed",
                        "answer": "replanned",
                    },
                )

        accepted = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": '"' * 1400, "title": '"' * 64},
                )
            ),
        )
        accepted.session.start()
        accepted.session.submit_user('"' * 1024)
        accepted.guest(
            "MODEL_REQUEST", model_request(1, user_content='"' * 1024)
        )
        accepted.wait_provider()
        self.assertEqual(accepted.codec.decode(accepted.lines[-1]).kind, "MODEL_RESPONSE")

        worst_goal = '"' * 1024
        worst_content = '"' * 1400
        worst_title = '"' * 64
        worst_result = {
            "status": 0,
            "value0": 0xFFFFFFFF,
            "value1": 0xFFFFFFFF,
            "value2": 0xFFFFFFFF,
            "result": "report_drafted;handle=4294967295",
            "model_authored_content": worst_content,
            "integrity_verified": True,
            "content_trust": "untrusted_model_derived",
        }
        worst_request = model_request(
            relay.MAX_SEQUENCE,
            turn_id=relay.MAX_SEQUENCE,
            request_id=relay.MAX_SEQUENCE,
            user_content=worst_goal,
        )
        worst_request["messages"][1]["content"] = (
            daemon.NEXUS_CONTROL_CONTEXT_PREFIX
            + "nexus-O|r=16|ctx=18446744073709551615|last=-2147483648/4294967295"
        )
        worst_request["messages"].extend(
            (
                {
                    "role": "assistant",
                    "tool_use": {
                        "corr_id": relay.MAX_SEQUENCE - 1,
                        "tool": "draft_report",
                        "arguments": {
                            "content": worst_content,
                            "title": worst_title,
                        },
                    },
                },
                {
                    "role": "tool",
                    "tool_corr_id": relay.MAX_SEQUENCE - 1,
                    "content": relay.canonical_json_bytes(worst_result).decode(
                        "utf-8"
                    ),
                    "is_error": False,
                },
            )
        )
        self.assertLessEqual(
            len(relay.canonical_json_bytes(worst_request)),
            daemon.NEXUS_MAX_MODEL_REQUEST_BYTES,
        )

    def test_semantic_tool_rejection_preserves_exact_history_for_replanning(self) -> None:
        arguments = {"handle": 0x1234}
        provider = ReplyProvider(
            relay.ModelReply("tool_use", tool="read_artifact", arguments=arguments),
            relay.ModelReply("final", content="used a fresh plan"),
        )
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        harness.session.submit_user("read the current report")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="read the current report"),
        )
        harness.wait_provider()

        inner = {
            "status": -2,
            "value0": 0,
            "value1_omitted": "volatile_payload_size",
            "value2": 0,
            "result": "stale_report_handle",
        }
        canonical = relay.canonical_json_bytes(inner).decode("utf-8")
        harness.guest(
            "TOOL_EVENT",
            {
                "turn_id": 1,
                "request_id": 1,
                "corr_id": 1,
                "tool": "read_artifact",
                "status": -2,
                "sequence": 1,
                "value0": 0,
                "value1": 0,
                "value2": 0,
                "result": "stale_report_handle",
                "context_seq": 2,
                "provenance": 0,
                "projection_sha256": "",
                "result_sha256": hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest(),
            },
        )
        followup = model_request(2, user_content="read the current report")
        followup["messages"].extend(
            (
                {
                    "role": "assistant",
                    "tool_use": {
                        "corr_id": 1,
                        "tool": "read_artifact",
                        "arguments": arguments,
                    },
                },
                {
                    "role": "tool",
                    "tool_corr_id": 1,
                    "content": canonical,
                    "is_error": True,
                },
            )
        )
        harness.guest("MODEL_REQUEST", followup)
        harness.wait_provider()
        self.assertEqual(
            harness.codec.decode(harness.lines[-1]).json_object()["content"],
            "used a fresh plan",
        )
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "used a fresh plan",
            },
        )

    def test_nexus_completion_is_byte_bound_to_delivered_final(self) -> None:
        boundary = "结" * 682 + "ok"
        self.assertEqual(len(boundary.encode("utf-8")), 2048)
        harness = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content=boundary))
        )
        harness.session.start()
        harness.session.submit_user("analyze")
        harness.guest("MODEL_REQUEST", model_request(1, user_content="analyze"))
        harness.wait_provider()
        delivered = harness.codec.decode(harness.lines[-1]).json_object()
        self.assertEqual(delivered["content"], boundary)

        with self.assertRaises(relay.WireProtocolError) as mismatch:
            harness.guest(
                "TURN_COMPLETE",
                {
                    "turn_id": 1,
                    "request_id": 1,
                    "status": "completed",
                    "answer": boundary[:-1] + "x",
                },
            )
        self.assertEqual(mismatch.exception.code, "FINAL_MISMATCH")
        with self.assertRaises(relay.WireProtocolError) as missing_counters:
            harness._raw_guest(
                "TURN_COMPLETE",
                {
                    "turn_id": 1,
                    "request_id": 1,
                    "status": "completed",
                    "answer": boundary,
                },
            )
        self.assertEqual(missing_counters.exception.code, "BAD_TURN")
        with self.assertRaises(relay.WireProtocolError) as wrong_rounds:
            harness.guest(
                "TURN_COMPLETE",
                {
                    "turn_id": 1,
                    "request_id": 1,
                    "status": "completed",
                    "answer": boundary,
                    "rounds": 0,
                    "retries": 0,
                    "attempts": 1,
                },
            )
        self.assertEqual(wrong_rounds.exception.code, "BAD_TURN")
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": boundary,
                "rounds": 1,
                "retries": 0,
                "attempts": 1,
            },
        )
        self.assertIsNone(harness.session.active)
        self.assertEqual(
            (
                harness.controller[-1]["rounds"],
                harness.controller[-1]["retries"],
                harness.controller[-1]["attempts"],
            ),
            (1, 0, 1),
        )

    def test_nexus_nul_final_is_retryable_before_delivery_and_can_replan(self) -> None:
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply("final", content="bad\0tail"),
                relay.ModelReply("final", content="clean final"),
            ),
        )
        harness.session.start()
        harness.session.submit_user("return a clean answer")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="return a clean answer"),
        )
        harness.wait_provider()
        rejected = harness.codec.decode(harness.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        self.assertEqual(rejected.json_object()["code"], "BAD_PROVIDER_RESPONSE")
        self.assertIs(rejected.json_object()["retryable"], True)
        self.assertFalse(harness.session._nexus_final_frozen)
        self.assertIsNone(harness.session._last_final_response)
        self.assertFalse(
            any(
                event.get("type") == "model_response" and event.get("corr_id") == 1
                for event in harness.controller
            )
        )

        harness.guest(
            "MODEL_REQUEST",
            model_request(2, user_content="return a clean answer"),
        )
        harness.wait_provider()
        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "clean final",
            },
        )
        self.assertEqual(harness.controller[-1]["type"], "turn_complete")

    def test_nexus_failed_completion_cannot_smuggle_answer_content(self) -> None:
        for status in ("cancelled", "error"):
            for field in ("answer", "content"):
                with self.subTest(status=status, field=field):
                    harness = SessionHarness("nexus")
                    harness.session.start()
                    harness.session.submit_user("stop")
                    with self.assertRaises(relay.WireProtocolError) as caught:
                        harness.guest(
                            "TURN_COMPLETE",
                            {
                                "turn_id": 1,
                                "request_id": 1,
                                "status": status,
                                field: "forged final",
                            },
                        )
                    self.assertEqual(caught.exception.code, "BAD_TURN")
                    self.assertIsNotNone(harness.session.active)

    def test_nexus_report_argument_expands_without_widening_agentlive(self) -> None:
        content = "x" * 2800
        nexus = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": content},
                )
            ),
        )
        nexus.session.start()
        nexus.session.submit_user("draft")
        nexus.guest("MODEL_REQUEST", model_request(1, user_content="draft"))
        nexus.wait_provider()
        response = nexus.codec.decode(nexus.lines[-1])
        self.assertEqual(response.kind, "MODEL_RESPONSE")
        self.assertEqual(response.json_object()["arguments"]["content"], content)

        legacy = SessionHarness(
            "agentlive",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": content},
                )
            ),
        )
        legacy.session.start()
        legacy.session.submit_user("draft")
        legacy.guest("MODEL_REQUEST", model_request(1, nexus=False))
        legacy.wait_provider()
        rejected = legacy.codec.decode(legacy.lines[-1])
        self.assertEqual(rejected.kind, "MODEL_ERROR")
        self.assertEqual(rejected.json_object()["code"], "MODEL_RESPONSE_INVALID")

    def test_model_error_message_obeys_guest_utf8_byte_cap(self) -> None:
        self.assertEqual(daemon.MAX_MODEL_ERROR_MESSAGE_BYTES, 240)

        def emitted_message(message: str, code: str = "TEST_ERROR") -> str:
            # This unit isolates the shared wire truncation helper.  Nexus
            # MODEL_ERROR additionally requires an authenticated request
            # outcome, which is covered by the replanning tests above.
            harness = SessionHarness("agentlive")
            harness.session._send_model_error(
                {"turn_id": 1, "request_id": 1, "corr_id": 1},
                relay.ProviderError(code, message, retryable=True),
            )
            frame = harness.codec.decode(harness.lines[-1])
            self.assertEqual(frame.kind, "MODEL_ERROR")
            payload = frame.json_object()
            self.assertTrue(payload["retryable"])
            self.assertEqual(
                payload["code"],
                code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) else "PROVIDER_FAILURE",
            )
            emitted = str(payload["message"])
            self.assertLessEqual(
                len(emitted.encode("utf-8")), daemon.MAX_MODEL_ERROR_MESSAGE_BYTES
            )
            return emitted

        ascii_boundary = "x" * daemon.MAX_MODEL_ERROR_MESSAGE_BYTES
        self.assertEqual(emitted_message(ascii_boundary), ascii_boundary)
        self.assertEqual(
            emitted_message(ascii_boundary + "x"),
            ascii_boundary,
        )

        chinese_boundary = "中" * (daemon.MAX_MODEL_ERROR_MESSAGE_BYTES // 3)
        self.assertEqual(emitted_message(chinese_boundary), chinese_boundary)
        partial_codepoint = "中" * 79 + "ab" + "文"
        truncated = emitted_message(partial_codepoint)
        self.assertEqual(truncated, "中" * 79 + "ab")
        self.assertEqual(emitted_message("bad\0tail"), "bad[NUL]tail")
        emitted_message("safe", "BAD\0CODE")

    def test_approval_timeout_is_agentlive_only(self) -> None:
        cases = (("agentlive", daemon.APPROVAL_TIMEOUT_SECONDS),)
        self.assertEqual(cases, (("agentlive", 25.0),))

        for profile, timeout in cases:
            with self.subTest(profile=profile):
                harness = SessionHarness(profile)
                harness.session.start()
                harness.session.submit_user("approve a tool")
                harness.session._last_model_response_corr = 1
                request = {
                    "turn_id": 1,
                    "request_id": 1,
                    "corr_id": 1,
                    "tool": "send_message",
                    "arguments_sha256": "a" * 64,
                    "nonce": "profile-timeout",
                }
                with mock.patch.object(daemon.time, "monotonic", return_value=100.0):
                    harness.session._approval_request(request)
                self.assertEqual(harness.session._approval_deadline, 100.0 + timeout)
                with mock.patch.object(
                    daemon.time,
                    "monotonic",
                    return_value=harness.session._approval_deadline - 0.001,
                ):
                    self.assertFalse(
                        harness.session.poll_approval(controller_available=True)
                    )
                with mock.patch.object(
                    daemon.time,
                    "monotonic",
                    return_value=harness.session._approval_deadline,
                ):
                    self.assertTrue(
                        harness.session.poll_approval(controller_available=True)
                    )

    def test_task_event_is_strict_and_observer_projection_is_metadata_only(self) -> None:
        attestor = SyntheticSourceAttestor()
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            source_attestor=attestor,
        )
        harness.session.start()
        harness.session.submit_user("coordinate specialists")
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="coordinate specialists"),
        )
        harness.wait_provider()
        _inner, tool = source_search_result(attestor.search_projection)
        emit_successful_child_task(harness, tool)
        controller = harness.controller[-1]
        self.assertEqual(controller["type"], "task_event")
        self.assertEqual(controller["agent_role"], "research")
        self.assertEqual(controller["workflow_lifecycle_id"], 3)
        self.assertEqual(controller["workflow_lifecycle_generation"], 2)
        self.assertEqual(controller["artifact_sha256"], tool["projection_sha256"])
        self.assertIn("controller-only", str(controller["summary"]))
        telemetry = harness.telemetry[-1]
        self.assertEqual(telemetry["type"], "telemetry")
        self.assertEqual(telemetry["event"], "artifact_published")
        self.assertEqual(telemetry["agent_role"], "research")
        self.assertEqual(telemetry["artifact_sha256"], tool["projection_sha256"])
        self.assertEqual(telemetry["resource_used"], 44)
        self.assertNotIn("summary", telemetry)
        self.assertNotIn("controller-only", str(telemetry))
        output = io.StringIO()
        cli.render_event(controller, output, json_events=False)
        rendered = output.getvalue()
        self.assertIn("published artifact", rendered)
        self.assertIn("research", rendered)
        self.assertIn("task=9", rendered)
        output = io.StringIO()
        observe.render_event(telemetry, output, json_events=False)
        rendered = output.getvalue()
        for expected in ("research", "9", "44", "artifact_published"):
            self.assertIn(expected, rendered)

        for malformed in (
            task_event(extra="no"),
            {key: value for key, value in task_event().items() if key != "source_pid"},
            {key: value for key, value in task_event().items() if key != "target_pid"},
            task_event(control_id_known=False, control_id=9),
            task_event(control_id_known=True),
            task_event(digest="A" * 64),
            task_event(role="planner"),
            task_event(workflow_lifecycle_id=0),
            task_event(workflow_lifecycle_generation=3),
            task_event(task_id=0),
            task_event(task_id=0x1_0000_0000),
            task_event(parent_task_id=0x1_0000_0000),
            task_event(summary="unsafe\x1b]0;title\x07"),
        ):
            with self.subTest(malformed=malformed), self.assertRaises(
                relay.WireProtocolError
            ):
                invalid = SessionHarness("nexus")
                invalid.session.start()
                invalid.session.submit_user("invalid task")
                invalid.guest("TASK_EVENT", malformed)

        agentlive = SessionHarness("agentlive")
        agentlive.session.start()
        line = harness.codec.encode_json(SESSION, 1, "TASK_EVENT", task_event())
        with self.assertRaises(relay.WireProtocolError):
            agentlive.session.handle_line(line)

    def test_real_control_identity_is_never_inferred_from_agent_id(self) -> None:
        harness = SessionHarness("nexus")
        harness.session.start()
        harness.session.submit_user("observe identity")
        harness._ensure_root_prelude(
            {"turn_id": 1, "request_id": 1, "corr_id": 1}
        )
        self.assertEqual(harness.controller[-1]["agent_control_id"], 0x100)
        self.assertEqual(harness.telemetry[-1]["agent_control_id"], 0x100)

        separate = SessionHarness("nexus")
        separate.session.start()
        separate.session.submit_user("unknown control identity")
        separate.guest(
            "TASK_EVENT",
            task_event(
                task_id=101,
                role="coordinator",
                agent_pid=5,
                agent_id=99,
                source_pid=5,
                target_pid=5,
            ),
        )
        self.assertNotIn("agent_control_id", separate.controller[-1])
        self.assertNotIn("agent_control_id", separate.telemetry[-1])

    def test_control_identity_accepts_full_u64_only_for_control_fields(self) -> None:
        for control_id in (1 << 63, (1 << 64) - 1):
            with self.subTest(control_id=control_id):
                harness = SessionHarness("nexus")
                harness.session.start()
                harness.session.submit_user("full width control identity")
                harness.session._bind_kernel_identity(
                    role="coordinator",
                    pid=5,
                    agent_id=1,
                    actor_control_id=control_id,
                )
                common = task_event(
                    task_id=101,
                    role="coordinator",
                    agent_pid=5,
                    agent_id=1,
                    control_id_known=True,
                    control_id=control_id,
                    source_pid=5,
                    target_pid=5,
                )
                harness.guest("TASK_EVENT", common)
                for projection in (harness.controller[-1], harness.telemetry[-1]):
                    self.assertEqual(projection["control_id"], control_id)
                    self.assertEqual(projection["agent_control_id"], control_id)

        for control_id in (0, 1 << 64):
            with self.subTest(rejected_control_id=control_id), self.assertRaises(
                relay.WireProtocolError
            ):
                harness = SessionHarness("nexus")
                harness.session.start()
                harness.session.submit_user("invalid control identity")
                harness.guest(
                    "TASK_EVENT",
                    task_event(
                        task_id=101,
                        role="coordinator",
                        agent_pid=5,
                        agent_id=1,
                        control_id_known=True,
                        control_id=control_id,
                        source_pid=5,
                        target_pid=5,
                    ),
                )

        observer = SessionHarness("nexus")
        observer.session._telemetry(
            {
                "event": "control_bounds",
                "control_id": 0,
                "agent_control_id": 1 << 63,
                "actor_control_id": (1 << 64) - 1,
                "corr_id": 1 << 63,
            }
        )
        self.assertEqual(observer.telemetry[-1]["control_id"], 0)
        self.assertEqual(observer.telemetry[-1]["agent_control_id"], 1 << 63)
        self.assertEqual(
            observer.telemetry[-1]["actor_control_id"], (1 << 64) - 1
        )
        self.assertNotIn("corr_id", observer.telemetry[-1])
        observer.session._telemetry(
            {
                "event": "control_overflow",
                "control_id": 1 << 64,
                "agent_control_id": 1 << 64,
                "actor_control_id": 1 << 64,
            }
        )
        for field in daemon.FULL_U64_CONTROL_FIELDS:
            self.assertNotIn(field, observer.telemetry[-1])
        observer.session._telemetry(
            {
                "event": "invalid_control_types",
                "control_id": -1,
                "agent_control_id": True,
                "actor_control_id": False,
            }
        )
        for field in daemon.FULL_U64_CONTROL_FIELDS:
            self.assertNotIn(field, observer.telemetry[-1])

        for field in ("corr_id", "workflow_lifecycle_id", "tick", "context_seq"):
            with self.subTest(still_signed_safe_field=field), self.assertRaises(
                relay.WireProtocolError
            ):
                harness = SessionHarness("nexus")
                harness.session.start()
                harness.session.submit_user("non-control bound")
                harness.guest("TASK_EVENT", task_event(**{field: 1 << 63}))

    def test_kernel_telemetry_sources_are_nexus_only_strict_and_not_host_spoofable(self) -> None:
        nexus = SessionHarness("nexus")
        nexus.session.start()
        nexus.session.submit_user("observe kernel")
        audit = {
            "event": "kernel_audit",
            "source": "kernel_audit",
            "fresh": True,
            "record_sequence": 17,
            "tick": 100,
            "workflow_lifecycle_id": 3,
            "workflow_lifecycle_generation": 2,
            "pid": 8,
            "agent_id": 3,
            "actor_control_id": 0x1234,
            "role": "analyst",
            "audit_kind": 2,
            "loop_state": 2,
            "tool_id": 1002,
            "event_type": 2,
            "source_pid": 8,
            "target_pid": 9,
            "status": 0,
            "value0": 41,
            "value1": 7,
            "value2": 9,
            "provenance": 0,
        }
        nexus.guest("TELEMETRY", audit)
        projected = nexus.telemetry[-1]
        self.assertEqual(projected["source"], "kernel_audit")
        self.assertIs(projected["fresh"], True)
        self.assertEqual(projected["record_sequence"], 17)
        for key in (
            "audit_kind",
            "event_type",
            "workflow_lifecycle_id",
            "workflow_lifecycle_generation",
            "actor_control_id",
            "source_pid",
            "target_pid",
            "value0",
            "value1",
            "value2",
        ):
            self.assertIn(key, projected)
        for secret in ("raw", "summary", "content"):
            self.assertNotIn(secret, projected)

        next_audit = {**audit, "record_sequence": 18, "tick": 101, "value0": 42}
        nexus.guest("TELEMETRY", next_audit)
        self.assertEqual(nexus.telemetry[-1]["record_sequence"], 18)

        snapshot = {
            "event": "kernel_snapshot",
            "source": "kernel_snapshot",
            "fresh": False,
            "tick": 102,
            "pid": 8,
            "agent_id": 3,
            "role": "analyst",
            "workflow_lifecycle_id": 3,
            "workflow_lifecycle_generation": 2,
            "loop_state": 2,
            "capability_mask": 0x3F,
            "context_seq": 9,
            "wait_sleep_delta": 5,
            "wait_wakeup_delta": 4,
            "sched_dispatch": 8,
            "sched_dispatch_count": 9,
            "sched_budget": 13,
            "sched_budget_used": 14,
            "sched_vruntime": 21,
            "actor_control_id": 0x1234,
        }
        nexus.guest(
            "TELEMETRY",
            snapshot,
        )
        self.assertEqual(nexus.telemetry[-1]["source"], "kernel_snapshot")
        self.assertEqual(nexus.telemetry[-1]["capability_mask"], 0x3F)
        self.assertNotIn("record_sequence", nexus.telemetry[-1])

        full_control = SessionHarness("nexus")
        full_control.session.start()
        full_control.session.submit_user("wide kernel identity")
        wide_audit = {**audit, "actor_control_id": (1 << 64) - 1}
        full_control.guest("TELEMETRY", wide_audit)
        full_control.guest(
            "TELEMETRY",
            {**snapshot, "actor_control_id": (1 << 64) - 1},
        )
        self.assertEqual(
            full_control.session._kernel_identities[8][3], (1 << 64) - 1
        )
        self.assertEqual(
            full_control.telemetry[-1]["actor_control_id"], (1 << 64) - 1
        )
        with self.assertRaises(relay.WireProtocolError):
            invalid_control = SessionHarness("nexus")
            invalid_control.session.start()
            invalid_control.session.submit_user("overflow kernel identity")
            invalid_control.guest(
                "TELEMETRY", {**audit, "actor_control_id": 1 << 64}
            )
        output = io.StringIO()
        observe.render_event(nexus.telemetry[-1], output, json_events=False)
        for expected in (
            "3:2",
            "4660",
            "5/4",
            "14",
            "21",
            "caps=0x3f",
            "kernel_snapshot",
        ):
            self.assertIn(expected, output.getvalue())

        output = io.StringIO()
        observe.render_event(projected, output, json_events=False)
        rendered_audit = output.getvalue()
        self.assertIn("17", rendered_audit)
        self.assertIn("3:2", rendered_audit)
        self.assertIn("1002", rendered_audit)
        self.assertIn("8->9", rendered_audit)

        for malformed in (
            {**audit, "fresh": False},
            {**audit, "record_sequence": 0},
            {**audit, "event": "kernel_snapshot"},
            {**audit, "record_sequence": 18},
            {**audit, "record_sequence": 16},
            {**audit, "reason": {"secret": "nested"}},
            {**audit, "raw": "business-data"},
            {**audit, "provenance": 32, "record_sequence": 19},
            {**audit, "workflow_lifecycle_generation": 3, "record_sequence": 19},
            {**snapshot, "fresh": True},
            {**snapshot, "record_sequence": 1},
            {key: value for key, value in snapshot.items() if key != "capability_mask"},
            {**snapshot, "capability_mask": 0},
            {key: value for key, value in snapshot.items() if key != "actor_control_id"},
            {**snapshot, "actor_control_id": 0x1235},
            {**snapshot, "wait_sleep_delta": 0},
            {**snapshot, "sched_dispatch_count": 7},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(
                relay.WireProtocolError
            ):
                nexus.guest("TELEMETRY", malformed)

        agentlive = SessionHarness("agentlive")
        agentlive.session.start()
        with self.assertRaises(relay.WireProtocolError):
            agentlive.guest("TELEMETRY", audit)

        nexus.session._telemetry(audit, source="kernel_audit")
        host_event = nexus.telemetry[-1]
        self.assertEqual(host_event["source"], "host")
        self.assertNotEqual(host_event["source"], "kernel_audit")

        nexus.guest(
            "TELEMETRY",
            {
                "event": "policy_note",
                "source": "guest_policy",
                "reason": "bounded_reason",
            },
        )
        self.assertEqual(nexus.telemetry[-1]["reason"], "bounded_reason")
        for unsafe_reason in ({"nested": "data"}, "bad\x1b[31m"):
            with self.assertRaises(relay.WireProtocolError):
                nexus.guest(
                    "TELEMETRY",
                    {
                        "event": "policy_note",
                        "source": "guest_policy",
                        "reason": unsafe_reason,
                    },
                )
        with self.assertRaises(relay.WireProtocolError) as malformed_tool:
            nexus.guest(
                "TOOL_EVENT",
                {
                    "turn_id": 1,
                    "request_id": 1,
                    "corr_id": 1,
                    "event": "tool_result",
                    "reason": {"paper": "observer-secret"},
                    "labels": {"raw": "observer-label-secret"},
                    "result": "controller-only-result",
                },
            )
        self.assertEqual(malformed_tool.exception.code, "BAD_TOOL_EVENT")
        self.assertNotIn("observer-secret", str(nexus.telemetry))

    def test_turn_completion_waits_boundedly_for_late_kernel_identity(self) -> None:
        harness = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="verified"))
        )
        harness.session.start()
        harness.session.submit_user("identity race")
        request = model_request(1, user_content="identity race")
        root = {
            "turn_id": 1,
            "request_id": 1,
            "corr_id": 1,
            "workflow_lifecycle_id": 3,
            "workflow_lifecycle_generation": 2,
            "task_id": 101,
            "parent_task_id": 0,
            "role": "coordinator",
            "agent_pid": 5,
            "agent_id": 1,
            "control_id_known": True,
            "control_id": 0x100,
            "source_pid": 5,
            "target_pid": 5,
            "status": 0,
        }
        for event, state, tick in (
            ("assigned", "assigned", 90),
            ("accepted", "accepted", 91),
            ("progress", "running", 92),
        ):
            harness._raw_guest(
                "TASK_EVENT",
                {**root, "event": event, "task_state": state, "tick": tick},
            )
        harness._raw_guest("MODEL_REQUEST", request)
        harness.wait_provider()
        harness._raw_guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "verified",
                "rounds": 1,
                "retries": 0,
                "attempts": 1,
            },
        )
        self.assertIsNotNone(harness.session._pending_turn_complete)
        self.assertFalse(
            any(event.get("type") == "turn_complete" for event in harness.controller)
        )
        with self.assertRaises(relay.WireProtocolError) as late:
            harness._raw_guest(
                "TASK_EVENT",
                {
                    **root,
                    "event": "progress",
                    "task_state": "running",
                    "tick": 201,
                },
            )
        self.assertEqual(late.exception.code, "TURN_PROOF_PENDING")

        harness.session._bind_kernel_identity(
            role="coordinator", pid=5, agent_id=1, actor_control_id=0x100
        )
        harness.session._retry_pending_turn_complete()
        self.assertIsNone(harness.session._pending_turn_complete)
        self.assertIsNone(harness.session.active)
        self.assertEqual(harness.controller[-1]["type"], "turn_complete")

        timeout = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="timeout"))
        )
        timeout.session.start()
        timeout.session.submit_user("identity timeout")
        request = model_request(1, user_content="identity timeout")
        for event, state, tick in (
            ("assigned", "assigned", 90),
            ("accepted", "accepted", 91),
            ("progress", "running", 92),
        ):
            timeout._raw_guest(
                "TASK_EVENT",
                {
                    **root,
                    "event": event,
                    "task_state": state,
                    "tick": tick,
                },
            )
        timeout._raw_guest("MODEL_REQUEST", request)
        timeout.wait_provider()
        timeout._raw_guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "timeout",
                "rounds": 1,
                "retries": 0,
                "attempts": 1,
            },
        )
        pending = timeout.session._pending_turn_complete
        assert pending is not None
        timeout.session._pending_turn_complete = (pending[0], pending[1], 0.0)
        with self.assertRaises(relay.WireProtocolError) as expired:
            timeout.session.poll_turn_proof()
        self.assertEqual(expired.exception.code, "TURN_PROOF_TIMEOUT")
        self.assertIsNone(timeout.session._pending_turn_complete)

    def test_nexus_controls_are_forwarded_but_agentlive_stays_closed(self) -> None:
        nexus = SessionHarness("nexus")
        nexus.session.start()
        for command in ("agents", "tasks", "artifacts"):
            nexus.session.request_control(command)
            self.assertEqual(nexus.codec.decode(nexus.lines[-1]).kind, "CONTROL_REQUEST")
        agentlive = SessionHarness("agentlive")
        agentlive.session.start()
        with self.assertRaises(relay.WireProtocolError):
            agentlive.session.request_control("agents")

    def test_interactive_provider_deadline_is_profile_specific(self) -> None:
        class DeadlineProvider:
            def __init__(self) -> None:
                self.remaining: float | None = None

            def complete(self, _request, *, deadline_monotonic=None):
                assert deadline_monotonic is not None
                self.remaining = deadline_monotonic - daemon.time.monotonic()
                return relay.ModelReply("final", content="done")

        for profile, expected in (
            ("agentlive", daemon.INTERACTIVE_PROVIDER_TIMEOUT_SECONDS),
            ("nexus", daemon.NEXUS_INTERACTIVE_PROVIDER_TIMEOUT_SECONDS),
        ):
            with self.subTest(profile=profile):
                provider = DeadlineProvider()
                harness = SessionHarness(profile, provider)
                harness.session.start()
                harness.session.submit_user("measure provider deadline")
                harness.guest(
                    "MODEL_REQUEST",
                    model_request(
                        1,
                        user_content="measure provider deadline",
                        nexus=profile == "nexus",
                    ),
                )
                harness.wait_provider()
                self.assertIsNotNone(provider.remaining)
                assert provider.remaining is not None
                self.assertGreater(provider.remaining, expected - 1.0)
                self.assertLessEqual(provider.remaining, expected)

    def test_nexus_rejects_approval_requests(self) -> None:
        harness = SessionHarness("nexus")
        harness.session.start()
        harness.session.submit_user("publish a report")
        with self.assertRaises(relay.WireProtocolError) as rejected:
            harness.session._approval_request({})
        self.assertEqual(rejected.exception.code, "BAD_APPROVAL")
        self.assertIsNone(harness.session.pending_approval)

    def test_nexus_cancelled_provider_isolated_from_new_turn_and_reset(self) -> None:
        class IsolatedProvider:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.calls = 0
                self.old_started = threading.Event()
                self.old_release = threading.Event()
                self.new_started = threading.Event()
                self.reset_calls = 0
                self.deadline_remaining: list[float] = []

            def complete(self, _request, *, deadline_monotonic=None):
                assert deadline_monotonic is not None
                self.deadline_remaining.append(
                    deadline_monotonic - daemon.time.monotonic()
                )
                with self.lock:
                    call = self.calls
                    self.calls += 1
                if call == 0:
                    self.old_started.set()
                    self.old_release.wait(2)
                    return relay.ModelReply("final", content="stale")
                if call == 1:
                    self.new_started.set()
                    return relay.ModelReply("final", content="fresh")
                raise AssertionError("unexpected provider call")

            def reset_session(self) -> None:
                self.reset_calls += 1

        provider = IsolatedProvider()
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        harness.session.submit_user("cancel slow model")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="cancel slow model")
        )
        self.assertTrue(provider.old_started.wait(1))
        self.assertTrue(harness.session.cancel())
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "cancelled"},
        )

        reset_request = harness.session.request_control("reset")
        self.assertEqual(reset_request, 2)
        harness.guest(
            "CONTROL_RESULT",
            {"request_id": reset_request, "command": "reset", "status": "ok"},
        )
        self.assertEqual(provider.reset_calls, 1)

        turn_id, request_id = harness.session.submit_user("start immediately")
        self.assertEqual((turn_id, request_id), (2, 3))
        harness.guest(
            "MODEL_REQUEST",
            model_request(
                2,
                turn_id=turn_id,
                request_id=request_id,
                user_content="start immediately",
            ),
        )
        self.assertTrue(provider.new_started.wait(1))
        harness.wait_provider()
        fresh = harness.codec.decode(harness.lines[-1])
        self.assertEqual(fresh.kind, "MODEL_RESPONSE")
        self.assertEqual(
            fresh.json_object(),
            {
                "turn_id": turn_id,
                "request_id": request_id,
                "corr_id": 2,
                "type": "final",
                "content": "fresh",
            },
        )
        self.assertEqual(harness.session._last_final_response, (2, "fresh"))

        delivered_line_count = len(harness.lines)
        provider.old_release.set()
        harness.wait_provider()
        self.assertEqual(len(harness.lines), delivered_line_count)
        self.assertEqual(harness.session._last_model_response_corr, 2)
        self.assertEqual(harness.session._last_final_response, (2, "fresh"))
        self.assertTrue(
            any(
                event.get("event") == "late_model_result_dropped"
                and event.get("corr_id") == 1
                for event in harness.telemetry
            )
        )
        self.assertFalse(
            any(
                event.get("type") == "model_response"
                and event.get("corr_id") == 1
                for event in harness.controller
            )
        )
        self.assertFalse(
            any(
                event.get("event") == "model_response"
                and event.get("corr_id") == 1
                for event in harness.telemetry
            )
        )
        self.assertFalse(
            any(
                event.get("event") == "provider_result"
                and event.get("corr_id") == 1
                for event in harness.telemetry
            )
        )
        self.assertEqual(len(provider.deadline_remaining), 2)
        for remaining in provider.deadline_remaining:
            self.assertGreater(
                remaining, daemon.NEXUS_INTERACTIVE_PROVIDER_TIMEOUT_SECONDS - 1.0
            )
            self.assertLessEqual(
                remaining, daemon.NEXUS_INTERACTIVE_PROVIDER_TIMEOUT_SECONDS
            )
        self.assertEqual(harness.session._provider_inflight, set())

        harness.guest(
            "TURN_COMPLETE",
            {
                "turn_id": turn_id,
                "request_id": request_id,
                "status": "completed",
                "answer": "fresh",
            },
        )
        self.assertIsNone(harness.session.active)

    def test_cancel_racing_with_model_request_preserves_guest_attempt_count(self) -> None:
        harness = SessionHarness("nexus", NeverProvider())
        harness.session.start()
        harness.session.submit_user("cancel before provider admission")
        self.assertTrue(harness.session.cancel())
        harness.guest(
            "MODEL_REQUEST",
            model_request(1, user_content="cancel before provider admission"),
        )
        turn = harness.session.active
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual((turn.attempts, turn.rounds, turn.retries), (1, 0, 0))
        dropped = next(
            event
            for event in harness.controller
            if event.get("type") == "model_request_dropped"
        )
        self.assertEqual((dropped["round"], dropped["attempt"]), (1, 1))
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "cancelled"},
        )
        self.assertEqual(
            (
                harness.controller[-1]["rounds"],
                harness.controller[-1]["retries"],
                harness.controller[-1]["attempts"],
            ),
            (0, 0, 1),
        )

    def test_late_cancel_does_not_replace_an_owned_terminal_outcome(self) -> None:
        attestor = SyntheticSourceAttestor()
        round_limit = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use",
                    tool="source_search",
                    arguments={"query": "symbol", "path_prefix": "os/"},
                )
            ),
            max_rounds=1,
            source_attestor=attestor,
        )
        round_limit.session.start()
        round_limit.session.submit_user("search once")
        round_limit.guest(
            "MODEL_REQUEST", model_request(1, user_content="search once")
        )
        round_limit.wait_provider()
        self.assertEqual(
            round_limit.session._nexus_task_ledger.snapshot().termination_cause,
            "round_limit",
        )
        self.assertTrue(round_limit.session.cancel())
        self.assertEqual(
            round_limit.session._nexus_task_ledger.snapshot().termination_cause,
            "round_limit",
        )
        _inner, tool = source_search_result(attestor.search_projection)
        emit_successful_child_task(round_limit, tool)
        round_limit.guest("TOOL_EVENT", tool)
        round_limit.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "cancelled"},
        )
        self.assertIsNone(round_limit.session.active)

        final = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="already final"))
        )
        final.session.start()
        final.session.submit_user("finish")
        final.guest("MODEL_REQUEST", model_request(1, user_content="finish"))
        final.wait_provider()
        final_digest = final.session._last_final_response_sha256
        self.assertTrue(final.session.cancel())
        self.assertEqual(final.session._last_final_response, (1, "already final"))
        self.assertEqual(final.session._last_final_response_sha256, final_digest)
        final.guest(
            "TURN_COMPLETE",
            {
                "turn_id": 1,
                "request_id": 1,
                "status": "completed",
                "answer": "already final",
            },
        )
        self.assertIsNone(final.session.active)

    def test_direct_tool_settlement_can_precede_user_cancel_root_ack(self) -> None:
        arguments = {"handle": 0x1234}
        harness = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply("tool_use", tool="read_artifact", arguments=arguments)
            ),
        )
        harness.session.start()
        harness.session.submit_user("read then stop")
        harness.guest(
            "MODEL_REQUEST", model_request(1, user_content="read then stop")
        )
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        inner = {
            "status": -2,
            "value0": 0,
            "value1_omitted": "volatile_payload_size",
            "value2": 0,
            "result": "stale_report_handle",
        }
        canonical = relay.canonical_json_bytes(inner)
        event = {
            "turn_id": 1,
            "request_id": 1,
            "corr_id": 1,
            "tool": "read_artifact",
            "status": -2,
            "sequence": 1,
            "value0": 0,
            "value1": 0,
            "value2": 0,
            "result": "stale_report_handle",
            "context_seq": 2,
            "provenance": 0,
            "projection_sha256": "",
            "result_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        wrong_corr = dict(event)
        wrong_corr["corr_id"] = 2
        with self.assertRaises(relay.WireProtocolError):
            harness.guest("TOOL_EVENT", wrong_corr)
        harness.guest("TOOL_EVENT", event)
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "cancelled"},
        )
        self.assertIsNone(harness.session.active)

    def test_nexus_cancelled_provider_isolation_has_a_hard_bound(self) -> None:
        class SaturatingProvider:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.calls = 0
                self.started = [threading.Event(), threading.Event()]
                self.release = threading.Event()

            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                with self.lock:
                    call = self.calls
                    self.calls += 1
                self.started[call].set()
                self.release.wait(2)
                return relay.ModelReply("final", content=f"late-{call}")

        provider = SaturatingProvider()
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        for turn_id in (1, 2):
            _, request_id = harness.session.submit_user(f"cancel {turn_id}")
            harness.guest(
                "MODEL_REQUEST",
                model_request(
                    turn_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    user_content=f"cancel {turn_id}",
                ),
            )
            self.assertTrue(provider.started[turn_id - 1].wait(1))
            self.assertTrue(harness.session.cancel())
            harness.guest(
                "TURN_COMPLETE",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "status": "cancelled",
                },
            )

        self.assertEqual(
            len(harness.session._provider_inflight),
            daemon.NEXUS_MAX_PROVIDER_INFLIGHT,
        )
        with self.assertRaises(relay.WireProtocolError) as saturated:
            harness.session.submit_user("third overlapping provider")
        self.assertEqual(saturated.exception.code, "PROVIDER_BUSY")
        self.assertEqual(harness.session.request_control("reset"), 3)

        provider.release.set()
        deadline = daemon.time.monotonic() + 2
        while harness.session._provider_inflight and daemon.time.monotonic() < deadline:
            harness.session.poll_provider()
            daemon.time.sleep(0.005)
        self.assertEqual(harness.session._provider_inflight, set())

    def test_protocol_provider_binds_exact_delivered_tool_history(self) -> None:
        tool_response = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "provider-call-1",
                                "type": "function",
                                "function": {
                                    "name": "publish_report",
                                    "arguments": '{"handle":12}',
                                },
                            }
                        ],
                    },
                }
            ]
        }
        final_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "done"},
                }
            ]
        }

        class StaticClient:
            endpoint = "https://api.example.com/v1/chat/completions"

            def __init__(self) -> None:
                self.responses = [tool_response, final_response]

            def post(self, _body, _headers, *, deadline_monotonic=None):
                del deadline_monotonic
                return self.responses.pop(0)

        def request_with_history(
            handle: int, tool: str = "publish_report"
        ) -> dict[str, object]:
            return relay.validate_guest_request(
                {
                    "corr_id": 2,
                    "model": "test-model",
                    "messages": [
                        {"role": "user", "content": "publish"},
                        {
                            "role": "assistant",
                            "tool_use": {
                                "corr_id": 1,
                                "tool": tool,
                                "arguments": {"handle": handle},
                            },
                        },
                        {"role": "tool", "tool_corr_id": 1, "content": "ok"},
                    ],
                    "tools": [],
                    "max_tokens": 64,
                },
                max_output_tokens=64,
            )

        client = StaticClient()
        provider = relay.OpenAICompatibleProvider(
            client, api_key="test-secret", model="test-model"  # type: ignore[arg-type]
        )
        provider.defer_call_commits()
        first_request = relay.validate_guest_request(
            {
                "corr_id": 1,
                "model": "test-model",
                "messages": [{"role": "user", "content": "publish"}],
                "tools": [
                    {
                        "name": "publish_report",
                        "description": "Publish a report",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "handle": {"type": "integer", "minimum": 1}
                            },
                            "required": ["handle"],
                            "additionalProperties": False,
                        },
                    }
                ],
                "max_tokens": 64,
            },
            max_output_tokens=64,
        )
        reply = provider.complete(first_request)
        with self.assertRaises(relay.ProviderError) as undelivered:
            provider.complete(request_with_history(12))
        self.assertEqual(undelivered.exception.code, "UNKNOWN_TOOL_CALL")
        provider.commit_model_reply(1, reply)
        with self.assertRaises(relay.ProviderError) as mutated_tool:
            provider.complete(request_with_history(12, "read_artifact"))
        self.assertEqual(mutated_tool.exception.code, "TOOL_CALL_MISMATCH")
        with self.assertRaises(relay.ProviderError) as mutated:
            provider.complete(request_with_history(99))
        self.assertEqual(mutated.exception.code, "TOOL_CALL_MISMATCH")
        self.assertEqual(provider.complete(request_with_history(12)).content, "done")

        compatibility_client = StaticClient()
        compatibility_provider = relay.OpenAICompatibleProvider(
            compatibility_client,  # type: ignore[arg-type]
            api_key="test-secret",
            model="test-model",
        )
        session = relay.RelaySession(
            compatibility_provider, goal="Publish exactly.", session=SESSION
        )
        first_line = session.codec.encode_json(SESSION, 1, "REQUEST", first_request)
        self.assertEqual(
            session.codec.decode(session.handle_line(first_line) or b"").kind,
            "RESPONSE",
        )
        second_line = session.codec.encode_json(
            SESSION, 2, "REQUEST", request_with_history(12)
        )
        second_response = session.codec.decode(session.handle_line(second_line) or b"")
        self.assertEqual(second_response.json_object()["type"], "final")

    def test_interactive_provider_mapping_commits_only_after_delivery(self) -> None:
        class TrackingProvider:
            def __init__(self, *, blocking: bool) -> None:
                self.blocking = blocking
                self.started = threading.Event()
                self.release = threading.Event()
                self.commits: list[tuple[int, str]] = []
                self.deferred = False

            def defer_call_commits(self) -> None:
                self.deferred = True

            def complete(self, _request, *, deadline_monotonic=None):
                del deadline_monotonic
                self.started.set()
                if self.blocking:
                    self.release.wait(2)
                return relay.ModelReply(
                    "tool_use",
                    tool="draft_report",
                    arguments={"content": "draft"},
                    provider_call_id="provider-call",
                )

            def commit_model_reply(self, corr_id, reply) -> None:
                self.commits.append((corr_id, reply.provider_call_id))

        delivered_provider = TrackingProvider(blocking=False)
        delivered = SessionHarness("nexus", delivered_provider)
        delivered.session.start()
        delivered.session.submit_user("publish")
        delivered.guest("MODEL_REQUEST", model_request(1))
        delivered.wait_provider()
        self.assertTrue(delivered_provider.deferred)
        self.assertEqual(delivered_provider.commits, [(1, "provider-call")])

        cancelled_provider = TrackingProvider(blocking=True)
        cancelled = SessionHarness("nexus", cancelled_provider)
        cancelled.session.start()
        cancelled.session.submit_user("cancel")
        cancelled.guest(
            "MODEL_REQUEST", model_request(1, user_content="cancel")
        )
        self.assertTrue(cancelled_provider.started.wait(1))
        cancelled.session.cancel()
        cancelled_provider.release.set()
        cancelled.wait_provider()
        self.assertEqual(cancelled_provider.commits, [])

    def test_replay_exhaustion_precedes_clean_session_closed_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "extra.jsonl"
            fixture.write_text(
                '{"response":{"type":"final","content":"unused"}}\n',
                encoding="utf-8",
            )
            provider = relay.ReplayProvider.from_jsonl(fixture)
            harness = SessionHarness("nexus", provider)
            harness.session.start()
            harness.session.close()
            with self.assertRaises(relay.RelayError):
                harness.guest("SESSION_CLOSED", {"reason": "guest_complete"})
            self.assertFalse(harness.session.closed)
            self.assertFalse(
                any(event.get("type") == "session_closed" for event in harness.controller)
            )

    def test_cli_escapes_terminal_controls_on_untrusted_controller_fields(self) -> None:
        hostile = "visible\x1b]0;spoofed\x07"
        events = (
            {"type": "tool_event", "tool": hostile, "status": 0, "result": hostile},
            {
                "type": "approval_request",
                "tool": "publish_report",
                "display": hostile,
            },
            {"type": "control_result", "command": "status", "result": hostile},
            {"type": "turn_complete", "turn_id": 1, "status": "completed", "answer": hostile},
        )
        for event in events:
            with self.subTest(kind=event["type"]):
                output = io.StringIO()
                cli.render_event(event, output, json_events=False)
                rendered = output.getvalue()
                self.assertNotIn("\x1b", rendered)
                self.assertNotIn("\x07", rendered)
                self.assertIn("\\x1b", rendered)
                self.assertIn("\\x07", rendered)

    @unittest.skipIf(os.name == "nt", "owner-only runtime mode is POSIX-specific")
    def test_profile_is_published_and_expectation_helpers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.chmod(base, 0o700)
            paths = local.prepare_runtime_paths("abcdef123456", base=base)
            local.publish_state(
                paths,
                session_id=SESSION,
                token="f" * 64,
                pid=12,
                provider="replay",
                model="",
                guest_profile="nexus",
            )
            state = local.load_state(paths.state_file)
            self.assertEqual(state["guest_profile"], "nexus")
            self.assertEqual(cli._guest_profile(state), "nexus")
            self.assertEqual(observe._guest_profile(state), "nexus")

        output = io.StringIO()
        with (
            mock.patch.object(
                cli.local,
                "load_state",
                return_value={"guest_profile": "agentlive"},
            ),
            mock.patch.object(cli.sys, "stderr", output),
        ):
            self.assertEqual(cli.main(["--expect-guest-profile", "nexus"]), 1)
        self.assertIn("unexpected Guest profile", output.getvalue())

        output = io.StringIO()
        with (
            mock.patch.object(
                observe.local,
                "load_state",
                return_value={"guest_profile": "agentlive"},
            ),
            mock.patch.object(observe.sys, "stderr", output),
        ):
            self.assertEqual(
                observe.main(["--expect-guest-profile", "nexus"]), 1
            )
        self.assertIn("unexpected Guest profile", output.getvalue())

    def test_combined_nexus_run_pins_cli_to_the_same_profile(self) -> None:
        daemon_args, client_args, runtime_dir = console._split_run_arguments(
            [
                "--provider=replay",
                "--guest-profile=nexus",
                "--expect-guest-profile=nexus",
                "--script=demo.txt",
                "--event-timeout=1",
                "--runtime-dir=/tmp/nexus",
            ]
        )
        self.assertEqual(
            client_args,
            [
                "--expect-guest-profile=nexus",
                "--script=demo.txt",
                "--event-timeout=1",
            ],
        )
        self.assertIn("--guest-profile=nexus", daemon_args)
        self.assertEqual(runtime_dir, Path("/tmp/nexus"))

        class FakeProcess:
            pid = 45
            returncode = 0

            def poll(self):
                return None

            def wait(self, timeout=None):
                del timeout
                return 0

        process = FakeProcess()
        with (
            mock.patch.object(console.subprocess, "Popen", return_value=process),
            mock.patch.object(console.local, "load_state", return_value={"pid": 45}),
            mock.patch.object(console.agentos_cli, "main", return_value=0) as client,
        ):
            self.assertEqual(
                console.run_combined(
                    ["--provider", "replay", "--guest-profile", "nexus"]
                ),
                0,
            )
        arguments = client.call_args.args[0]
        self.assertIn("--expect-guest-profile", arguments)
        self.assertEqual(
            arguments[arguments.index("--expect-guest-profile") + 1], "nexus"
        )


if __name__ == "__main__":
    unittest.main()
