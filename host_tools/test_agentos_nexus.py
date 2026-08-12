#!/usr/bin/env python3
"""Focused Host tests for the additive AgentOS Nexus Guest profile."""

from __future__ import annotations

import io
import hashlib
import os
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


class BlockingProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, _request, *, deadline_monotonic=None):
        del deadline_monotonic
        self.started.set()
        self.release.wait(2)
        return relay.ModelReply("final", content="late")


class SessionHarness:
    def __init__(
        self, profile: str, provider=None, *, max_rounds: int | None = None
    ) -> None:
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
            guest_profile=profile,
        )
        kinds = (
            tuple(daemon.NEXUS_WIRE_KINDS)
            if profile == "nexus"
            else tuple(relay.WIRE_V2_KINDS)
        )
        self.codec = relay.FrameCodec(
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=kinds,
        )
        self.guest_seq = 1

    def guest(self, kind: str, payload: dict[str, object]) -> None:
        line = self.codec.encode_json(SESSION, self.guest_seq, kind, payload)
        self.guest_seq += 1
        self.session.handle_line(line)

    def wait_provider(self) -> None:
        deadline = daemon.time.monotonic() + 2
        while daemon.time.monotonic() < deadline:
            if self.session.poll_provider():
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
        "status": 0,
        "tick": 100,
    }
    value.update(updates)
    return value


def approval_request(
    *,
    handle: int = 12,
    corr_id: int = 1,
    nonce: str = "approval",
    issued_tick: int = 100,
    expires_tick: int = 220,
) -> dict[str, object]:
    arguments = {"handle": handle}
    canonical = relay.canonical_json_bytes(arguments).decode("utf-8")
    return {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": corr_id,
        "tool": "publish_report",
        "tool_id": 1004,
        "arguments": arguments,
        "canonical_arguments": canonical,
        "arguments_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "nonce": nonce,
        "issued_tick": issued_tick,
        "expires_tick": expires_tick,
    }


def model_request(corr_id: int) -> dict[str, object]:
    return {
        "turn_id": 1,
        "request_id": 1,
        "corr_id": corr_id,
        "model": "test-model",
        "messages": [{"role": "user", "content": "publish"}],
        "tools": [],
        "max_tokens": 64,
    }


class NexusProfileTests(unittest.TestCase):
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
                "max_payload": relay.PROTOCOL_MAX_PAYLOAD_BYTES,
                "max_rounds": daemon.NEXUS_MAX_ROUNDS,
                "max_tokens": relay.DEFAULT_MAX_OUTPUT_TOKENS,
                "guest_profile": "nexus",
                "features": ["task_event_v1"],
            },
        )
        self.assertEqual(
            daemon.NEXUS_READY_LINE,
            b"agentnexus_ucore: relay_ready=1 nexus=1",
        )
        self.assertEqual(nexus.controller[-1]["guest_profile"], "nexus")

        delegate_arguments = {
            "role": "analyst",
            "task_type": "inspect",
            "objective": "summarize",
            "input_handle": 11,
            "secondary_handle": 12,
            "extra": 13,
        }

        def model_boundary(profile: str, count: int, *, replayed: bool = False):
            arguments = dict(list(delegate_arguments.items())[:count])
            response = {
                "type": "tool_use",
                "tool": "delegate_task",
                "arguments": arguments,
            }
            provider = (
                relay.ReplayProvider([relay.ReplayRecord(response)])
                if replayed
                else ReplyProvider(
                    relay.ModelReply(
                        "tool_use", tool="delegate_task", arguments=arguments
                    )
                )
            )
            boundary = SessionHarness(profile, provider)
            boundary.session.start()
            boundary.session.submit_user(f"delegate with {count} arguments")
            boundary.guest("MODEL_REQUEST", model_request(1))
            boundary.wait_provider()
            return boundary.codec.decode(boundary.lines[-1])

        for count in (3, 4):
            with self.subTest(profile="nexus", arguments=count):
                self.assertEqual(model_boundary("nexus", count).kind, "MODEL_RESPONSE")
        # Exercise the replay adapter used by the real offline demo at the
        # five-field Analyst boundary, not only an in-memory reply.
        self.assertEqual(
            model_boundary("nexus", 5, replayed=True).kind,
            "MODEL_RESPONSE",
        )
        nexus_overflow = model_boundary("nexus", 6)
        self.assertEqual(nexus_overflow.kind, "MODEL_ERROR")
        self.assertEqual(nexus_overflow.json_object()["code"], "BAD_TOOL_ARGUMENTS")

        self.assertEqual(model_boundary("agentlive", 3).kind, "MODEL_RESPONSE")
        legacy_overflow = model_boundary("agentlive", 4)
        self.assertEqual(legacy_overflow.kind, "MODEL_ERROR")
        self.assertEqual(legacy_overflow.json_object()["code"], "BAD_TOOL_ARGUMENTS")

    def test_round_policy_is_resolved_from_guest_profile(self) -> None:
        self.assertEqual(SessionHarness("agentlive").session.max_rounds, 8)
        self.assertEqual(
            SessionHarness("nexus").session.max_rounds,
            daemon.NEXUS_MAX_ROUNDS,
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

    def test_model_error_message_obeys_guest_utf8_byte_cap(self) -> None:
        self.assertEqual(daemon.MAX_MODEL_ERROR_MESSAGE_BYTES, 240)

        def emitted_message(message: str) -> str:
            harness = SessionHarness("nexus")
            harness.session._send_model_error(
                {"turn_id": 1, "request_id": 1, "corr_id": 1},
                relay.ProviderError("TEST_ERROR", message, retryable=True),
            )
            frame = harness.codec.decode(harness.lines[-1])
            self.assertEqual(frame.kind, "MODEL_ERROR")
            payload = frame.json_object()
            self.assertTrue(payload["retryable"])
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

    def test_approval_timeout_is_resolved_from_guest_profile(self) -> None:
        cases = (
            ("agentlive", daemon.APPROVAL_TIMEOUT_SECONDS),
            ("nexus", daemon.NEXUS_APPROVAL_TIMEOUT_SECONDS),
        )
        self.assertEqual(cases, (("agentlive", 25.0), ("nexus", 90.0)))

        for profile, timeout in cases:
            with self.subTest(profile=profile):
                harness = SessionHarness(profile)
                harness.session.start()
                harness.session.submit_user("approve a tool")
                harness.session._last_model_response_corr = 1
                if profile == "nexus":
                    request = approval_request(nonce="profile-timeout")
                    harness.session._last_nexus_tool_use = (
                        1,
                        "publish_report",
                        str(request["canonical_arguments"]),
                    )
                else:
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
        harness = SessionHarness("nexus")
        harness.session.start()
        harness.session.submit_user("coordinate specialists")
        harness.guest(
            "TASK_EVENT",
            task_event(
                event="artifact_published",
                task_state="completed",
                artifact_handle=12,
                digest="a" * 64,
                context_seq=9,
                provenance=62,
                resource_used=44,
                summary="bounded controller-only artifact summary",
            ),
        )
        controller = harness.controller[-1]
        self.assertEqual(controller["type"], "task_event")
        self.assertEqual(controller["agent_role"], "analyst")
        self.assertEqual(controller["workflow_lifecycle_id"], 3)
        self.assertEqual(controller["workflow_lifecycle_generation"], 2)
        self.assertEqual(controller["artifact_sha256"], "a" * 64)
        self.assertIn("controller-only", str(controller["summary"]))
        telemetry = harness.telemetry[-1]
        self.assertEqual(telemetry["type"], "telemetry")
        self.assertEqual(telemetry["event"], "artifact_published")
        self.assertEqual(telemetry["agent_role"], "analyst")
        self.assertEqual(telemetry["artifact_sha256"], "a" * 64)
        self.assertEqual(telemetry["resource_used"], 44)
        self.assertNotIn("summary", telemetry)
        self.assertNotIn("controller-only", str(telemetry))
        output = io.StringIO()
        cli.render_event(controller, output, json_events=False)
        rendered = output.getvalue()
        self.assertIn("published artifact", rendered)
        self.assertIn("analyst", rendered)
        self.assertIn("task=7", rendered)
        output = io.StringIO()
        observe.render_event(telemetry, output, json_events=False)
        rendered = output.getvalue()
        for expected in ("analyst", "7", "44", "artifact_published"):
            self.assertIn(expected, rendered)

        for malformed in (
            task_event(extra="no"),
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
                harness.guest("TASK_EVENT", malformed)

        agentlive = SessionHarness("agentlive")
        agentlive.session.start()
        line = harness.codec.encode_json(SESSION, 1, "TASK_EVENT", task_event())
        with self.assertRaises(relay.WireProtocolError):
            agentlive.session.handle_line(line)

    def test_real_control_identity_is_never_inferred_from_agent_id(self) -> None:
        harness = SessionHarness("nexus")
        harness.session.start()
        harness.session.submit_user("observe identity")
        harness.guest(
            "TASK_EVENT",
            task_event(control_id_known=True, control_id=0x102030405),
        )
        self.assertEqual(harness.controller[-1]["agent_control_id"], 0x102030405)
        self.assertEqual(harness.telemetry[-1]["agent_control_id"], 0x102030405)

        separate = SessionHarness("nexus")
        separate.session.start()
        separate.session.submit_user("unknown control identity")
        separate.guest("TASK_EVENT", task_event(agent_id=99))
        self.assertNotIn("agent_control_id", separate.controller[-1])
        self.assertNotIn("agent_control_id", separate.telemetry[-1])

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
        self.assertIn("observer-secret", str(nexus.controller[-1]))
        self.assertNotIn("observer-secret", str(nexus.telemetry[-1]))
        self.assertNotIn("observer-label-secret", str(nexus.telemetry[-1]))
        self.assertNotIn("reason", nexus.telemetry[-1])
        self.assertNotIn("labels", nexus.telemetry[-1])

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

    def test_nexus_session_approval_is_scoped_to_tool_and_argument_digest(self) -> None:
        harness = SessionHarness("nexus")
        harness.session.start()
        harness.session.submit_user("publish a report")
        harness.session._last_model_response_corr = 1
        first = approval_request(nonce="first")
        harness.session._last_nexus_tool_use = (
            1,
            "publish_report",
            str(first["canonical_arguments"]),
        )
        harness.session._approval_request(dict(first))
        binding = {
            key: first[key] for key in daemon.NEXUS_APPROVAL_BINDING_FIELDS
        }
        self.assertEqual(
            {
                key: harness.controller[-1][key]
                for key in ("tool_id", "issued_tick", "expires_tick")
            },
            {"tool_id": 1004, "issued_tick": 100, "expires_tick": 220},
        )
        harness.session.decide_approval("session", binding)
        self.assertIn(
            (
                "nexus",
                "publish_report",
                1004,
                first["arguments_sha256"],
            ),
            harness.session.session_approvals,
        )
        self.assertNotIn("publish_report", harness.session.session_approvals)

        same = approval_request(
            nonce="same-arguments", issued_tick=230, expires_tick=350
        )
        harness.session._last_model_response_corr = 1
        harness.session._last_nexus_tool_use = (
            1,
            "publish_report",
            str(same["canonical_arguments"]),
        )
        harness.session._approval_request(same)
        self.assertIsNone(harness.session.pending_approval)
        auto = harness.codec.decode(harness.lines[-1])
        self.assertEqual(auto.kind, "APPROVAL_DECISION")
        self.assertEqual(auto.json_object()["decision"], "session")
        self.assertEqual(auto.json_object()["nonce"], "same-arguments")
        self.assertEqual(auto.json_object()["issued_tick"], 230)
        self.assertEqual(auto.json_object()["expires_tick"], 350)

        changed = approval_request(
            handle=13,
            nonce="changed-arguments",
            issued_tick=360,
            expires_tick=480,
        )
        harness.session._last_model_response_corr = 1
        harness.session._last_nexus_tool_use = (
            1,
            "publish_report",
            str(changed["canonical_arguments"]),
        )
        harness.session._approval_request(changed)
        self.assertEqual(
            harness.session.pending_approval["arguments_sha256"],
            changed["arguments_sha256"],
        )
        self.assertEqual(harness.controller[-1]["type"], "approval_request")

    def test_nexus_approval_requires_and_exactly_echoes_tool_and_tick_binding(self) -> None:
        def pending_request() -> tuple[SessionHarness, dict[str, object]]:
            harness = SessionHarness("nexus")
            harness.session.start()
            harness.session.submit_user("publish a report")
            harness.session._last_model_response_corr = 1
            request = approval_request(nonce="bound-request")
            harness.session._last_nexus_tool_use = (
                1,
                "publish_report",
                str(request["canonical_arguments"]),
            )
            return harness, request

        invalid_updates: tuple[dict[str, object], ...] = (
            {"tool_id": 0},
            {"tool_id": 1003},
            {"expires_tick": 100},
            {"expires_tick": 99},
            {"arguments": {"handle": 99}},
            {"canonical_arguments": '{"handle":99}'},
        )
        for update in invalid_updates:
            with self.subTest(update=update):
                harness, request = pending_request()
                request.update(update)
                with self.assertRaises(relay.WireProtocolError):
                    harness.session._approval_request(request)
        for missing in ("tool_id", "issued_tick", "expires_tick"):
            with self.subTest(missing=missing):
                harness, request = pending_request()
                del request[missing]
                with self.assertRaises(relay.WireProtocolError):
                    harness.session._approval_request(request)

        harness, request = pending_request()
        harness.session._approval_request(request)

        class FakeConnection:
            guest_profile = "nexus"
            approval_binding: dict[str, object] | None = None

            def __init__(self) -> None:
                self.values: list[dict[str, object]] = []

            def send(self, value: dict[str, object]) -> None:
                self.values.append(value)

        connection = FakeConnection()
        cli._capture_approval_binding(connection, harness.controller[-1])  # type: ignore[arg-type]
        cli._send_approval(connection, "once")  # type: ignore[arg-type]
        expected_binding = {
            key: request[key] for key in daemon.NEXUS_APPROVAL_BINDING_FIELDS
        }
        self.assertEqual(
            connection.values,
            [{"type": "approval", "decision": "once", **expected_binding}],
        )

        for key, wrong in (
            ("nonce", "stale-nonce"),
            ("tool_id", 1003),
            ("issued_tick", 101),
            ("expires_tick", 221),
        ):
            with self.subTest(stale_field=key):
                stale = {**expected_binding, key: wrong}
                with self.assertRaises(relay.WireProtocolError) as caught:
                    harness.session.decide_approval("once", stale)
                self.assertEqual(caught.exception.code, "STALE_APPROVAL")
                self.assertIsNotNone(harness.session.pending_approval)

        harness.session.decide_approval("once", expected_binding)
        decision = harness.codec.decode(harness.lines[-1])
        self.assertEqual(decision.kind, "APPROVAL_DECISION")
        self.assertEqual(
            decision.json_object(),
            {**expected_binding, "decision": "once"},
        )

    def test_nexus_approval_is_bound_to_latest_model_tool_use(self) -> None:
        final = SessionHarness(
            "nexus", ReplyProvider(relay.ModelReply("final", content="no side effect"))
        )
        final.session.start()
        final.session.submit_user("do not publish")
        final.guest("MODEL_REQUEST", model_request(1))
        final.wait_provider()
        with self.assertRaises(relay.WireProtocolError) as caught:
            final.session._approval_request(approval_request())
        self.assertEqual(caught.exception.code, "BAD_APPROVAL")
        self.assertIsNone(final.session.pending_approval)

        newer = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use", tool="publish_report", arguments={"handle": 12}
                ),
                relay.ModelReply("final", content="newer response"),
            ),
        )
        newer.session.start()
        newer.session.submit_user("publish")
        newer.guest("MODEL_REQUEST", model_request(1))
        newer.wait_provider()
        old_approval = approval_request()
        newer.guest("MODEL_REQUEST", model_request(2))
        with self.assertRaises(relay.WireProtocolError) as stale:
            newer.session._approval_request(old_approval)
        self.assertEqual(stale.exception.code, "BAD_APPROVAL")
        newer.wait_provider()

        pending = SessionHarness(
            "nexus",
            ReplyProvider(
                relay.ModelReply(
                    "tool_use", tool="publish_report", arguments={"handle": 12}
                )
            ),
        )
        pending.session.start()
        pending.session.submit_user("publish")
        pending.guest("MODEL_REQUEST", model_request(1))
        pending.wait_provider()
        pending.session._approval_request(approval_request())
        with self.assertRaises(relay.WireProtocolError) as busy:
            pending.session._model_request(model_request(2))
        self.assertEqual(busy.exception.code, "APPROVAL_BUSY")

    def test_nexus_cancelled_provider_serializes_new_turn_and_reset(self) -> None:
        provider = BlockingProvider()
        harness = SessionHarness("nexus", provider)
        harness.session.start()
        harness.session.submit_user("cancel slow model")
        harness.guest("MODEL_REQUEST", model_request(1))
        self.assertTrue(provider.started.wait(1))
        self.assertTrue(harness.session.cancel())
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": 1, "request_id": 1, "status": "cancelled"},
        )
        with self.assertRaises(relay.WireProtocolError) as submit_busy:
            harness.session.submit_user("must wait")
        self.assertEqual(submit_busy.exception.code, "PROVIDER_BUSY")
        with self.assertRaises(relay.WireProtocolError) as reset_busy:
            harness.session.request_control("reset")
        self.assertEqual(reset_busy.exception.code, "PROVIDER_BUSY")

        provider.release.set()
        harness.wait_provider()
        turn_id, request_id = harness.session.submit_user("provider is idle")
        self.assertEqual((turn_id, request_id), (2, 2))

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
                "tools": [],
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
                    tool="publish_report",
                    arguments={"handle": 12},
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
        cancelled.guest("MODEL_REQUEST", model_request(1))
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
