#!/usr/bin/env python3
"""Focused tests for the persistent AgentOS console Host control plane."""

from __future__ import annotations

import io
import os
import queue
import socket
import stat
import sys
import tempfile
import threading
import time
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


SESSION = "0123456789abcdef0123456789abcdef"


def model_request(turn: int, request_id: int, corr: int) -> dict[str, object]:
    return {
        "turn_id": turn,
        "request_id": request_id,
        "corr_id": corr,
        "model": "test-model",
        "messages": [{"role": "user", "content": f"turn {turn}"}],
        "tools": [],
        "max_tokens": 64,
    }


class QueueProvider:
    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, object]] = []
        self.deadlines: list[float | None] = []

    def complete(self, request, *, deadline_monotonic=None):
        self.deadlines.append(deadline_monotonic)
        self.requests.append(dict(request))
        return self.replies.pop(0)


class BlockingProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, request, *, deadline_monotonic=None):
        del request, deadline_monotonic
        self.started.set()
        self.release.wait(2)
        return relay.ModelReply("final", content="late")


class SessionHarness:
    def __init__(self, provider) -> None:
        self.lines: list[bytes] = []
        self.controller: list[dict[str, object]] = []
        self.telemetry: list[dict[str, object]] = []
        self.session = daemon.InteractiveSession(
            provider,
            send_line=self.lines.append,
            controller_sink=lambda value: self.controller.append(dict(value)),
            telemetry_sink=lambda value: self.telemetry.append(dict(value)),
            session_id=SESSION,
        )
        self.codec = relay.FrameCodec(
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=tuple(relay.WIRE_V2_KINDS),
        )
        self.guest_seq = 1

    def guest(self, kind: str, value: dict[str, object]) -> None:
        line = self.codec.encode_json(SESSION, self.guest_seq, kind, value)
        self.guest_seq += 1
        self.session.handle_line(line)

    def kinds(self) -> list[str]:
        return [self.codec.decode(line).kind for line in self.lines]

    def wait_provider(self) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.session.poll_provider():
                return
            time.sleep(0.005)
        raise AssertionError("provider did not complete")


class LocalProtocolTests(unittest.TestCase):
    def test_strict_ndjson_rejects_duplicates_nan_crlf_and_partial_overflow(self) -> None:
        for raw in (
            b'{"a":1,"a":2}\n',
            b'{"a":NaN}\n',
            b'[]\n',
            b'{"a":1}\r\n',
        ):
            with self.subTest(raw=raw), self.assertRaises(local.LocalProtocolError):
                local.decode_message(raw)
        reader = local.NdjsonReader(max_line_bytes=128)
        with self.assertRaises(local.LocalProtocolError):
            reader.feed(b"x" * 128)

    def test_incremental_reader_handles_multiple_messages(self) -> None:
        reader = local.NdjsonReader()
        self.assertEqual(reader.feed(b'{"a":1}\n{"b"'), [{"a": 1}])
        self.assertEqual(reader.feed(b':2}\n'), [{"b": 2}])
        reader.finish()

    def test_interactive_replay_requires_a_bound_request_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            replay_file = Path(directory) / "replay.jsonl"
            replay_file.write_text(
                '{"response":{"type":"final","content":"ok"}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires request_sha256"):
                relay.ReplayProvider.from_jsonl(
                    replay_file, require_request_digests=True
                )
            # The one-shot V1 path intentionally retains legacy fixture support.
            provider = relay.ReplayProvider.from_jsonl(replay_file)
            self.assertIsInstance(provider, relay.ReplayProvider)

    @unittest.skipIf(not hasattr(socket, "AF_UNIX"), "AF_UNIX unavailable")
    def test_runtime_socket_and_state_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.chmod(base, 0o700)
            paths = local.prepare_runtime_paths("abcdef123456", base=base)
            server = local.bind_owner_socket(paths.control_socket)
            try:
                self.assertEqual(stat.S_IMODE(paths.directory.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(paths.control_socket.stat().st_mode), 0o600)
                local.publish_state(
                    paths,
                    session_id=SESSION,
                    token="a" * 64,
                    pid=123,
                    provider="replay",
                    model="",
                )
                self.assertEqual(stat.S_IMODE(paths.state_file.stat().st_mode), 0o600)
                self.assertEqual(local.load_state(paths.state_file)["pid"], 123)
            finally:
                server.close()

    @unittest.skipIf(os.name == "nt", "POSIX flock assertion")
    def test_runtime_lock_rejects_second_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = local.RuntimeLock.acquire(path)
            try:
                with self.assertRaises(local.LocalProtocolError):
                    local.RuntimeLock.acquire(path)
            finally:
                first.close()


class InteractiveSessionTests(unittest.TestCase):
    def test_two_turns_share_one_hello_and_correlation_is_session_wide(self) -> None:
        provider = QueueProvider(
            [relay.ModelReply("final", content="one"), relay.ModelReply("final", content="two")]
        )
        harness = SessionHarness(provider)
        harness.session.start()
        self.assertEqual(harness.kinds(), ["HELLO"])
        for turn, corr, answer in ((1, 1, "one"), (2, 2, "two")):
            turn_id, request_id = harness.session.submit_user(f"goal {turn}")
            self.assertEqual(turn_id, turn)
            harness.guest("MODEL_REQUEST", model_request(turn_id, request_id, corr))
            harness.wait_provider()
            harness.guest(
                "TURN_COMPLETE",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "status": "completed",
                    "answer": answer,
                },
            )
        self.assertEqual(harness.kinds().count("HELLO"), 1)
        self.assertEqual(harness.kinds().count("USER_MESSAGE"), 2)
        self.assertEqual(harness.kinds().count("MODEL_RESPONSE"), 2)
        self.assertEqual([request["corr_id"] for request in provider.requests], [1, 2])
        digests = [event["request_sha256"] for event in harness.controller if event["type"] == "model_request"]
        self.assertEqual(len(digests), 2)
        self.assertTrue(all(len(str(item)) == 64 for item in digests))

    def test_cancel_drops_late_provider_result_without_closing_session(self) -> None:
        provider = BlockingProvider()
        harness = SessionHarness(provider)
        harness.session.start()
        turn, request_id = harness.session.submit_user("cancel me")
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        self.assertTrue(provider.started.wait(1))
        self.assertTrue(harness.session.cancel())
        provider.release.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if harness.session.poll_provider():
                break
            time.sleep(0.005)
        self.assertNotIn("MODEL_RESPONSE", harness.kinds())
        self.assertIn("CANCEL", harness.kinds())
        self.assertTrue(
            any(item.get("event") == "late_model_result_dropped" for item in harness.telemetry)
        )
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": turn, "request_id": request_id, "status": "cancelled"},
        )
        self.assertIsNone(harness.session.active)
        self.assertFalse(harness.session.closed)

    def test_model_request_racing_a_fast_cancel_is_consumed_without_provider(self) -> None:
        provider = QueueProvider([relay.ModelReply("final", content="next turn")])
        harness = SessionHarness(provider)
        harness.session.start()
        turn, request_id = harness.session.submit_user("cancel before request")
        self.assertTrue(harness.session.cancel())
        before = len(harness.lines)
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        self.assertEqual(len(harness.lines), before)
        self.assertEqual(provider.requests, [])
        self.assertTrue(
            any(
                item.get("event") == "model_request_after_cancel_dropped"
                for item in harness.telemetry
            )
        )
        harness.guest(
            "TURN_COMPLETE",
            {"turn_id": turn, "request_id": request_id, "status": "cancelled"},
        )
        next_turn, next_request = harness.session.submit_user("continue")
        harness.guest("MODEL_REQUEST", model_request(next_turn, next_request, 2))
        harness.wait_provider()
        self.assertEqual(len(provider.requests), 1)
        self.assertIn("MODEL_RESPONSE", harness.kinds())

    def test_provider_reply_contract_failure_becomes_correlated_model_error(self) -> None:
        provider = QueueProvider(
            [
                relay.ModelReply(
                    "tool_use",
                    tool="query_file",
                    arguments={"a": 1, "b": 2, "c": 3, "d": 4},
                ),
                relay.ModelReply("final", content="recovered"),
            ]
        )
        harness = SessionHarness(provider)
        harness.session.start()
        turn, request_id = harness.session.submit_user("recover a bad provider reply")
        started = time.monotonic()
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        harness.wait_provider()
        error = harness.codec.decode(harness.lines[-1])
        self.assertEqual(error.kind, "MODEL_ERROR")
        self.assertEqual(
            {key: error.json_object()[key] for key in ("turn_id", "request_id", "corr_id")},
            {"turn_id": turn, "request_id": request_id, "corr_id": 1},
        )
        self.assertEqual(error.json_object()["code"], "BAD_TOOL_ARGUMENTS")
        self.assertGreater(provider.deadlines[0] or 0, started)
        self.assertLessEqual(
            (provider.deadlines[0] or 0) - started,
            daemon.INTERACTIVE_PROVIDER_TIMEOUT_SECONDS + 1,
        )

        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 2))
        harness.wait_provider()
        self.assertEqual(harness.codec.decode(harness.lines[-1]).kind, "MODEL_RESPONSE")

    def test_approval_is_exact_bound_and_replay_is_rejected(self) -> None:
        harness = SessionHarness(
            QueueProvider(
                [relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 7})]
            )
        )
        harness.session.start()
        turn, request_id = harness.session.submit_user("send")
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        harness.wait_provider()
        approval = {
            "turn_id": turn,
            "request_id": request_id,
            "corr_id": 1,
            "tool": "send_message",
            "arguments_sha256": "a" * 64,
            "nonce": "nonce-1",
            "display": "target=7",
        }
        harness.guest("APPROVAL_REQUEST", approval)
        binding = {
            key: approval[key] for key in daemon.APPROVAL_BINDING_FIELDS
        }
        harness.session.decide_approval("once", binding)
        frame = harness.codec.decode(harness.lines[-1])
        self.assertEqual(frame.kind, "APPROVAL_DECISION")
        self.assertEqual(
            set(frame.json_object()),
            {
                "turn_id",
                "request_id",
                "corr_id",
                "tool",
                "arguments_sha256",
                "nonce",
                "decision",
            },
        )
        with self.assertRaises(relay.WireProtocolError):
            harness.guest("APPROVAL_REQUEST", approval)

    def test_stale_approval_cannot_cross_turns(self) -> None:
        harness = SessionHarness(QueueProvider([relay.ModelReply("final", content="done")]))
        harness.session.start()
        turn, request_id = harness.session.submit_user("first")
        stale = {
            "turn_id": turn,
            "request_id": request_id,
            "corr_id": 1,
            "tool": "send_message",
            "arguments_sha256": "b" * 64,
            "nonce": "stale",
        }
        with self.assertRaises(relay.WireProtocolError):
            harness.guest("APPROVAL_REQUEST", stale)

    def test_timed_out_approval_input_cannot_approve_a_new_binding(self) -> None:
        harness = SessionHarness(
            QueueProvider(
                [
                    relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 7}),
                    relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 8}),
                ]
            )
        )
        harness.session.start()
        turn, request_id = harness.session.submit_user("two approvals")
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        harness.wait_provider()
        first = {
            "turn_id": turn,
            "request_id": request_id,
            "corr_id": 1,
            "tool": "send_message",
            "arguments_sha256": "1" * 64,
            "nonce": "first",
        }
        harness.guest("APPROVAL_REQUEST", first)
        self.assertTrue(harness.session.poll_approval(controller_available=False))

        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 2))
        harness.wait_provider()
        second = {
            "turn_id": turn,
            "request_id": request_id,
            "corr_id": 2,
            "tool": "send_message",
            "arguments_sha256": "2" * 64,
            "nonce": "second",
        }
        harness.guest("APPROVAL_REQUEST", second)
        stale_binding = {key: first[key] for key in daemon.APPROVAL_BINDING_FIELDS}
        with self.assertRaises(relay.WireProtocolError) as caught:
            harness.session.decide_approval("once", stale_binding)
        self.assertEqual(caught.exception.code, "STALE_APPROVAL")
        self.assertEqual(harness.session.pending_approval["nonce"], "second")
        harness.session.decide_approval(
            "once", {key: second[key] for key in daemon.APPROVAL_BINDING_FIELDS}
        )

    def test_observer_metadata_redacts_tool_result_and_marks_source(self) -> None:
        harness = SessionHarness(QueueProvider([relay.ModelReply("final", content="unused")]))
        harness.session.start()
        harness.guest(
            "TOOL_EVENT",
            {
                "event": "tool_result",
                "tool": "read_file",
                "corr_id": 1,
                "status": 0,
                "result": {"secret": "must-not-reach-observer"},
            },
        )
        self.assertIn("must-not-reach-observer", str(harness.controller[-1]))
        self.assertNotIn("must-not-reach-observer", str(harness.telemetry[-1]))
        self.assertNotIn("result", harness.telemetry[-1])
        self.assertEqual(harness.telemetry[-1]["source"], "guest")
        harness.guest(
            "TELEMETRY",
            {
                "event": "kernel_timeline",
                "source": "context_timeline",
                "fresh": True,
                "record_sequence": 9,
                "tool": "read_file",
                "context_seq": 9,
                "result": {"secret": "timeline-secret"},
                "raw": "timeline-raw-secret",
            },
        )
        timeline = harness.telemetry[-1]
        self.assertEqual(timeline["source"], "context_timeline")
        self.assertIs(timeline["fresh"], True)
        self.assertEqual(timeline["record_sequence"], 9)
        self.assertNotIn("result", timeline)
        self.assertNotIn("raw", timeline)
        self.assertNotIn("timeline-secret", str(timeline))
        harness.session._telemetry({"event": "host_marker"})
        self.assertEqual(harness.telemetry[-1]["source"], "host")

        with self.assertRaises(relay.WireProtocolError):
            harness.guest(
                "TELEMETRY",
                {
                    "event": "kernel_timeline",
                    "source": "context_timeline",
                    "fresh": False,
                    "record_sequence": 10,
                },
            )

    def test_signal_close_request_is_consumed_outside_serial_send(self) -> None:
        service = daemon.InteractiveRelayDaemon.__new__(daemon.InteractiveRelayDaemon)
        service.close_requested = threading.Event()
        service.stop_requested = threading.Event()
        service._signal_close_started = False
        service._shutdown_deadline = 0.0
        service.shutdown_grace_seconds = 1.0
        lines: list[bytes] = []

        def send_line(line: bytes) -> None:
            lines.append(line)
            if len(lines) >= 2:
                service.request_stop()

        service.session = daemon.InteractiveSession(
            QueueProvider([relay.ModelReply("final", content="unused")]),
            send_line=send_line,
            controller_sink=lambda _value: None,
            telemetry_sink=lambda _value: None,
            session_id=SESSION,
        )
        codec = relay.FrameCodec(
            wire_prefix=relay.WIRE_V2_PREFIX,
            wire_kinds=tuple(relay.WIRE_V2_KINDS),
        )
        service.session.start()
        service.session.submit_user("active")
        self.assertEqual([codec.decode(line).kind for line in lines], ["HELLO", "USER_MESSAGE"])
        self.assertTrue(service._consume_close_request())
        self.assertFalse(service._consume_close_request())
        frames = [codec.decode(line) for line in lines]
        self.assertEqual([frame.kind for frame in frames], ["HELLO", "USER_MESSAGE", "CANCEL"])
        self.assertEqual([frame.seq for frame in frames], [1, 2, 3])

    def test_pending_approval_fails_closed_without_controller_or_at_deadline(self) -> None:
        for controller_available in (False, True):
            with self.subTest(controller_available=controller_available):
                harness = SessionHarness(
                    QueueProvider(
                        [relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 7})]
                    )
                )
                harness.session.start()
                turn, request_id = harness.session.submit_user("send")
                harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
                harness.wait_provider()
                harness.guest(
                    "APPROVAL_REQUEST",
                    {
                        "turn_id": turn,
                        "request_id": request_id,
                        "corr_id": 1,
                        "tool": "send_message",
                        "arguments_sha256": "c" * 64,
                        "nonce": "timeout",
                    },
                )
                if controller_available:
                    harness.session._approval_deadline = 0
                self.assertTrue(
                    harness.session.poll_approval(
                        controller_available=controller_available
                    )
                )
                decision = harness.codec.decode(harness.lines[-1])
                self.assertEqual(decision.kind, "APPROVAL_DECISION")
                self.assertEqual(decision.json_object()["decision"], "deny")

    def test_approval_deadline_is_enforced_at_decision_time(self) -> None:
        def pending(nonce: str):
            harness = SessionHarness(
                QueueProvider(
                    [relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 7})]
                )
            )
            harness.session.start()
            turn, request_id = harness.session.submit_user("send")
            harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
            harness.wait_provider()
            request = {
                "turn_id": turn,
                "request_id": request_id,
                "corr_id": 1,
                "tool": "send_message",
                "arguments_sha256": "e" * 64,
                "nonce": nonce,
            }
            harness.guest("APPROVAL_REQUEST", request)
            binding = {
                key: request[key] for key in daemon.APPROVAL_BINDING_FIELDS
            }
            return harness, binding, harness.session._approval_deadline

        before, before_binding, deadline = pending("before")
        with mock.patch.object(daemon.time, "monotonic", return_value=deadline - 0.001):
            before.session.decide_approval("once", before_binding)
        accepted = before.codec.decode(before.lines[-1]).json_object()
        self.assertEqual(accepted["decision"], "once")

        for offset, choice in ((0.0, "once"), (0.001, "session")):
            with self.subTest(offset=offset, choice=choice):
                harness, binding, deadline = pending(f"expired-{choice}")
                with (
                    mock.patch.object(
                        daemon.time, "monotonic", return_value=deadline + offset
                    ),
                    self.assertRaises(relay.WireProtocolError) as caught,
                ):
                    harness.session.decide_approval(choice, binding)
                self.assertEqual(caught.exception.code, "APPROVAL_TIMEOUT")
                denied = harness.codec.decode(harness.lines[-1])
                self.assertEqual(denied.kind, "APPROVAL_DECISION")
                self.assertEqual(denied.json_object()["decision"], "deny")
                self.assertNotIn("send_message", harness.session.session_approvals)
                self.assertIsNone(harness.session.pending_approval)
                with self.assertRaises(relay.WireProtocolError) as second:
                    harness.session.decide_approval(choice, binding)
                self.assertEqual(second.exception.code, "NO_APPROVAL")

    def test_approval_arriving_after_cancel_does_not_queue_a_late_decision(self) -> None:
        harness = SessionHarness(
            QueueProvider(
                [relay.ModelReply("tool_use", tool="send_message", arguments={"arg0": 7})]
            )
        )
        harness.session.start()
        turn, request_id = harness.session.submit_user("send")
        harness.guest("MODEL_REQUEST", model_request(turn, request_id, 1))
        harness.wait_provider()
        self.assertTrue(harness.session.cancel())
        before = len(harness.lines)
        harness.guest(
            "APPROVAL_REQUEST",
            {
                "turn_id": turn,
                "request_id": request_id,
                "corr_id": 1,
                "tool": "send_message",
                "arguments_sha256": "d" * 64,
                "nonce": "after-cancel",
            },
        )
        self.assertEqual(len(harness.lines), before)
        self.assertIsNone(harness.session.pending_approval)
        self.assertTrue(
            any(
                item.get("event") == "approval_after_cancel_ignored"
                for item in harness.telemetry
            )
        )

    def test_shutdown_requires_guest_ack(self) -> None:
        harness = SessionHarness(QueueProvider([relay.ModelReply("final", content="unused")]))
        harness.session.start()
        harness.session.close()
        self.assertTrue(harness.session.closing)
        self.assertFalse(harness.session.closed)
        harness.guest("SESSION_CLOSED", {"reason": "guest_complete"})
        self.assertTrue(harness.session.closed)

    def test_guest_cannot_send_host_direction_kind(self) -> None:
        harness = SessionHarness(QueueProvider([relay.ModelReply("final", content="unused")]))
        harness.session.start()
        line = harness.codec.encode_json(SESSION, 1, "USER_MESSAGE", {"bad": True})
        with self.assertRaises(relay.WireProtocolError):
            harness.session.handle_line(line)


class LocalEndpointAndRenderingTests(unittest.TestCase):
    def test_stale_controller_queue_is_dropped_in_memory(self) -> None:
        class FakePeer:
            role = "controller"

            def __init__(self) -> None:
                self.closed = threading.Event()
                self.sent = []

            def send(self, value):
                self.sent.append(value)
                return True

        class FakeEndpoints:
            def __init__(self, current) -> None:
                self.current = current
                self.inbound = queue.Queue()

            def is_current(self, peer):
                return peer is self.current and not peer.closed.is_set()

        class FakeSession:
            def __init__(self) -> None:
                self.calls = []

            def submit_user(self, content):
                self.calls.append(("user", content))

            def cancel(self):
                self.calls.append(("cancel",))
                return True

            def decide_approval(self, decision, binding):
                self.calls.append(("approval", decision, binding))

        old = FakePeer()
        current = FakePeer()
        old.closed.set()
        endpoints = FakeEndpoints(current)
        binding = {
            "turn_id": 1,
            "request_id": 1,
            "corr_id": 1,
            "tool": "send_message",
            "arguments_sha256": "a" * 64,
            "nonce": "old",
        }
        for message in (
            {"type": "user_message", "content": "old goal"},
            {"type": "cancel"},
            {"type": "approval", "decision": "once", **binding},
            {"type": "session_close"},
        ):
            endpoints.inbound.put((old, message))
        endpoints.inbound.put(
            (current, {"type": "user_message", "content": "new goal"})
        )
        service = daemon.InteractiveRelayDaemon.__new__(daemon.InteractiveRelayDaemon)
        service.endpoints = endpoints
        service.session = FakeSession()
        closes = []
        service.initiate_close = closes.append
        service._drain_local()
        self.assertEqual(service.session.calls, [("user", "new goal")])
        self.assertEqual(closes, [])

    @unittest.skipIf(not hasattr(socket, "AF_UNIX") or os.name == "nt", "POSIX AF_UNIX test")
    def test_replaced_controller_cannot_execute_queued_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.chmod(base, 0o700)
            paths = local.prepare_runtime_paths("abcdef123456", base=base)
            endpoints = daemon.LocalEndpoints(paths, "f" * 64)
            endpoints.start()
            lines: list[bytes] = []
            service = daemon.InteractiveRelayDaemon.__new__(daemon.InteractiveRelayDaemon)
            service.endpoints = endpoints
            service.stop_requested = threading.Event()
            service._shutdown_deadline = 0.0
            service.shutdown_grace_seconds = 1.0
            service.session = daemon.InteractiveSession(
                QueueProvider([relay.ModelReply("final", content="unused")]),
                send_line=lines.append,
                controller_sink=endpoints.send_controller,
                telemetry_sink=endpoints.broadcast,
                session_id=SESSION,
            )
            service.session.start()

            def connect_controller():
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(paths.control_socket))
                client.sendall(
                    local.encode_message(
                        {
                            "type": "hello",
                            "protocol": 1,
                            "role": "controller",
                            "token": "f" * 64,
                        }
                    )
                )
                stream = client.makefile("rb")
                self.assertEqual(local.recv_one(stream)["type"], "welcome")
                return client, stream

            old, old_stream = connect_controller()
            binding = {
                "turn_id": 1,
                "request_id": 1,
                "corr_id": 1,
                "tool": "send_message",
                "arguments_sha256": "a" * 64,
                "nonce": "old",
            }
            old.sendall(
                b"".join(
                    local.encode_message(message)
                    for message in (
                        {"type": "user_message", "content": "old goal"},
                        {"type": "cancel"},
                        {"type": "approval", "decision": "once", **binding},
                        {"type": "session_close"},
                    )
                )
            )
            old_stream.close()
            old.close()
            deadline = time.monotonic() + 2
            while (
                (endpoints.has_controller() or endpoints.inbound.qsize() < 4)
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            self.assertFalse(endpoints.has_controller())
            self.assertGreaterEqual(endpoints.inbound.qsize(), 4)

            current, current_stream = connect_controller()
            try:
                service._drain_local()
                self.assertIsNone(service.session.active)
                self.assertFalse(service.session.closing)
                codec = relay.FrameCodec(
                    wire_prefix=relay.WIRE_V2_PREFIX,
                    wire_kinds=tuple(relay.WIRE_V2_KINDS),
                )
                self.assertEqual([codec.decode(line).kind for line in lines], ["HELLO"])

                current.sendall(
                    local.encode_message(
                        {"type": "user_message", "content": "new goal"}
                    )
                )
                deadline = time.monotonic() + 2
                while endpoints.inbound.empty() and time.monotonic() < deadline:
                    time.sleep(0.005)
                service._drain_local()
                self.assertIsNotNone(service.session.active)
                self.assertEqual(
                    [codec.decode(line).kind for line in lines],
                    ["HELLO", "USER_MESSAGE"],
                )
            finally:
                current_stream.close()
                current.close()
                endpoints.close()

    def test_cli_close_errors_are_not_masked(self) -> None:
        class FakeConnection:
            def __init__(self, events) -> None:
                self.events = list(events)
                self.values = []
                self.approval_binding = None

            def next(self, _timeout=None):
                return self.events.pop(0)

            def send(self, value):
                self.values.append(value)

            def close(self):
                pass

        for source_text in ("/quit\n", ""):
            with self.subTest(source=source_text or "EOF"):
                connection = FakeConnection(
                    [
                        {"type": "welcome", "role": "controller"},
                        {"type": "session_ready", "session_id": SESSION},
                        {"type": "error", "code": "CLOSE_FAILED", "message": "failed"},
                    ]
                )
                output = io.StringIO()
                with (
                    mock.patch.object(cli.local, "load_state", return_value={"provider": "replay"}),
                    mock.patch.object(cli.local, "connect_from_state", return_value=object()),
                    mock.patch.object(cli, "ConsoleConnection", return_value=connection),
                    mock.patch.object(cli, "_lines", return_value=(io.StringIO(source_text), True)),
                    mock.patch.object(cli.sys, "stdout", output),
                    mock.patch.object(cli.sys, "stderr", io.StringIO()),
                ):
                    result = cli.main(["--state-file", "unused"])
                self.assertEqual(result, 1)
                self.assertEqual(connection.values[-1], {"type": "session_close"})

    def test_cli_ctrl_c_at_approval_cancels_the_turn(self) -> None:
        binding = {
            "turn_id": 1,
            "request_id": 1,
            "corr_id": 1,
            "tool": "send_message",
            "arguments_sha256": "a" * 64,
            "nonce": "nonce",
        }

        class FakeConnection:
            def __init__(self) -> None:
                self.events = [
                    {"type": "welcome", "role": "controller"},
                    {"type": "session_ready", "session_id": SESSION},
                    {"type": "approval_request", **binding},
                    {"type": "turn_complete", "turn_id": 1, "status": "cancelled"},
                    {"type": "session_closed"},
                ]
                self.values = []
                self.approval_binding = None

            def next(self, _timeout=None):
                return self.events.pop(0)

            def send(self, value):
                self.values.append(value)

            def close(self):
                pass

        connection = FakeConnection()
        with (
            mock.patch.object(cli.local, "load_state", return_value={"provider": "replay"}),
            mock.patch.object(cli.local, "connect_from_state", return_value=object()),
            mock.patch.object(cli, "ConsoleConnection", return_value=connection),
            mock.patch.object(cli, "_lines", return_value=(sys.stdin, False)),
            mock.patch("builtins.input", side_effect=["goal", KeyboardInterrupt(), EOFError()]),
            mock.patch.object(cli.sys, "stdout", io.StringIO()),
            mock.patch.object(cli.sys, "stderr", io.StringIO()),
        ):
            result = cli.main(["--state-file", "unused"])
        self.assertEqual(result, 0)
        self.assertIn({"type": "cancel"}, connection.values)
        self.assertNotIn(
            {"type": "approval", "decision": "deny", **binding},
            connection.values,
        )

    def test_combined_propagates_daemon_failure_and_cleans_boot_interrupt(self) -> None:
        class FakeProcess:
            pid = 4321
            returncode = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                del timeout
                return 7

        process = FakeProcess()
        with (
            mock.patch.object(console.subprocess, "Popen", return_value=process),
            mock.patch.object(console.local, "load_state", return_value={"pid": process.pid}),
            mock.patch.object(console.agentos_cli, "main", return_value=0),
        ):
            self.assertEqual(console.run_combined(["--provider", "replay"]), 7)

        with (
            mock.patch.object(console.subprocess, "Popen", return_value=process),
            mock.patch.object(console.local, "load_state", side_effect=KeyboardInterrupt),
            mock.patch.object(console, "_terminate_child_group") as terminate,
        ):
            self.assertEqual(console.run_combined(["--provider", "replay"]), 130)
            terminate.assert_called_once_with(process)

    def test_local_close_arms_and_expires_a_bounded_shutdown(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.closed = False
                self.ready = True
                self.reasons = []

            def close(self, reason):
                self.reasons.append(reason)

        class FakePeer:
            role = "controller"

            def send(self, _message):
                return True

        service = daemon.InteractiveRelayDaemon.__new__(
            daemon.InteractiveRelayDaemon
        )
        service.session = FakeSession()
        service.stop_requested = threading.Event()
        service._shutdown_deadline = 0.0
        service.shutdown_grace_seconds = 0.01
        service._handle_local(FakePeer(), {"type": "session_close"})
        self.assertEqual(service.session.reasons, ["user_requested"])
        self.assertGreater(service._shutdown_deadline, time.monotonic())
        self.assertFalse(service.stop_requested.is_set())
        time.sleep(0.02)
        self.assertTrue(service._expire_shutdown_if_needed())
        self.assertTrue(service.stop_requested.is_set())

        failing = daemon.InteractiveRelayDaemon.__new__(
            daemon.InteractiveRelayDaemon
        )
        failing.session = FakeSession()
        failing.session.close = lambda _reason: (_ for _ in ()).throw(
            relay.RelayError("QEMU_WRITE_TIMEOUT", "blocked close writer")
        )
        failing.stop_requested = threading.Event()
        failing._shutdown_deadline = 0.0
        failing.shutdown_grace_seconds = 0.01
        with self.assertRaises(relay.RelayError):
            failing.initiate_close("user_requested")
        self.assertGreater(failing._shutdown_deadline, time.monotonic())

    def test_serial_writer_has_a_hard_deadline(self) -> None:
        class Blocked:
            def __init__(self) -> None:
                self.release = threading.Event()

            def write(self, _line):
                self.release.wait(1)

        process = Blocked()
        try:
            with self.assertRaises(relay.RelayError) as caught:
                relay._write_process_before_deadline(  # type: ignore[arg-type]
                    process, b"frame", deadline_monotonic=time.monotonic() + 0.02
                )
            self.assertEqual(caught.exception.code, "QEMU_WRITE_TIMEOUT")
        finally:
            process.release.set()

    @unittest.skipIf(os.name == "nt" or not hasattr(socket, "AF_UNIX"), "POSIX daemon test")
    def test_fake_qemu_runs_two_turns_in_one_boot_and_clean_shutdown(self) -> None:
        class QueueStream:
            def __init__(self) -> None:
                self.values: queue.Queue[bytes | None] = queue.Queue()

            def read(self, _count=-1):
                value = self.values.get()
                return b"" if value is None else value

            def put(self, value: bytes) -> None:
                self.values.put(value)

            def close(self) -> None:
                self.values.put(None)

        class FakeProc:
            def __init__(self) -> None:
                self.stdout = QueueStream()
                self.stderr = QueueStream()
                self.returncode = None

            def poll(self):
                return self.returncode

        class FakeQemu:
            def __init__(self) -> None:
                self.proc = FakeProc()
                self.codec = relay.FrameCodec(
                    wire_prefix=relay.WIRE_V2_PREFIX,
                    wire_kinds=tuple(relay.WIRE_V2_KINDS),
                )
                self.guest_seq = 1
                self.corr = 0
                self.starts = 0
                self.stopped = False

            def start(self):
                self.starts += 1
                self.proc.stdout.put(relay.GUEST_RELAY_READY_LINE + b"\n")
                return self.proc

            def emit(self, kind, payload):
                self.proc.stdout.put(
                    self.codec.encode_json(SESSION, self.guest_seq, kind, payload)
                )
                self.guest_seq += 1

            def write(self, line):
                frame = self.codec.decode(line)
                payload = frame.json_object()
                if frame.kind == "HELLO":
                    self.emit(
                        "TELEMETRY",
                        {
                            "event": "session_ready",
                            "turn_id": 0,
                            "request_id": 0,
                            "corr_id": 0,
                            "tick": 1,
                            "pid": 4,
                            "state": 1,
                            "status": 0,
                        },
                    )
                elif frame.kind == "USER_MESSAGE":
                    self.corr += 1
                    self.emit(
                        "MODEL_REQUEST",
                        model_request(payload["turn_id"], payload["request_id"], self.corr),
                    )
                elif frame.kind == "MODEL_RESPONSE":
                    self.emit(
                        "TURN_COMPLETE",
                        {
                            "turn_id": payload["turn_id"],
                            "request_id": payload["request_id"],
                            "status": "completed",
                            "answer": payload["content"],
                        },
                    )
                elif frame.kind == "SESSION_CLOSE":
                    self.emit("SESSION_CLOSED", {"reason": "guest_complete"})

            def stop(self):
                self.stopped = True
                self.proc.returncode = 0
                self.proc.stdout.close()
                self.proc.stderr.close()

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.chmod(base, 0o700)
            paths = local.prepare_runtime_paths("abcdef123456", base=base)
            fake = FakeQemu()
            service = daemon.InteractiveRelayDaemon(
                fake,  # type: ignore[arg-type]
                QueueProvider(
                    [
                        relay.ModelReply("final", content="answer one"),
                        relay.ModelReply("final", content="answer two"),
                    ]
                ),
                paths=paths,
                token="e" * 64,
                session_id=SESSION,
                provider_name="replay",
                model_name="",
                max_payload=4096,
                max_rounds=8,
                max_tokens=128,
                boot_timeout=2,
                quiet=True,
            )
            worker = threading.Thread(target=service.run)
            worker.start()
            deadline = time.monotonic() + 2
            while not paths.state_file.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            state = local.load_state(paths.state_file)
            controller = local.connect_from_state(state, role="controller")
            stream = controller.makefile("rb")
            self.assertEqual(local.recv_one(stream).get("type"), "welcome")

            def receive(kind: str):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    item = local.recv_one(stream)
                    if item.get("type") == kind:
                        return item
                raise AssertionError(f"missing {kind}")

            for number in (1, 2):
                controller.sendall(
                    local.encode_message({"type": "user_message", "content": f"goal {number}"})
                )
                complete = receive("turn_complete")
                self.assertEqual(complete["turn_id"], number)
            controller.sendall(local.encode_message({"type": "session_close"}))
            self.assertEqual(receive("session_closed")["reason"], "guest_complete")
            worker.join(2)
            stream.close()
            controller.close()
            self.assertFalse(worker.is_alive())
            self.assertEqual(fake.starts, 1)
            self.assertTrue(fake.stopped)

    @unittest.skipIf(not hasattr(socket, "AF_UNIX") or os.name == "nt", "POSIX AF_UNIX test")
    def test_endpoint_auth_roles_single_controller_and_multiple_observers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.chmod(base, 0o700)
            paths = local.prepare_runtime_paths("abcdef123456", base=base)
            endpoints = daemon.LocalEndpoints(paths, "f" * 64)
            endpoints.start()

            def connect(path: Path, role: str, token: str = "f" * 64):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(path))
                client.sendall(
                    local.encode_message(
                        {"type": "hello", "protocol": 1, "role": role, "token": token}
                    )
                )
                stream = client.makefile("rb")
                return client, stream, local.recv_one(stream)

            clients = []
            try:
                controller, controller_stream, welcome = connect(
                    paths.control_socket, "controller"
                )
                clients.append((controller, controller_stream))
                self.assertEqual(welcome, {"type": "welcome", "role": "controller"})
                second, second_stream, rejected = connect(paths.control_socket, "controller")
                clients.append((second, second_stream))
                self.assertEqual(rejected.get("code"), "CONTROLLER_BUSY")
                observer1, stream1, welcome1 = connect(paths.telemetry_socket, "observer")
                observer2, stream2, welcome2 = connect(paths.telemetry_socket, "observer")
                clients.extend(((observer1, stream1), (observer2, stream2)))
                self.assertEqual(welcome1.get("role"), "observer")
                self.assertEqual(welcome2.get("role"), "observer")
                wrong, wrong_stream, denied = connect(paths.control_socket, "observer")
                clients.append((wrong, wrong_stream))
                self.assertEqual(denied.get("code"), "AUTH")
                endpoints.broadcast({"type": "telemetry", "event": "test"})
                self.assertEqual(local.recv_one(stream1).get("event"), "test")
                self.assertEqual(local.recv_one(stream2).get("event"), "test")
                stream1.close()
                observer1.close()
                deadline = time.monotonic() + 1
                while endpoints.has_controller() and time.monotonic() < deadline:
                    endpoints.send_controller({"type": "controller_alive"})
                    self.assertEqual(
                        local.recv_one(controller_stream).get("type"),
                        "controller_alive",
                    )
                    break
                self.assertTrue(endpoints.has_controller())
            finally:
                for client, stream in clients:
                    stream.close()
                    client.close()
                endpoints.close()

    def test_slow_peer_queue_is_bounded_and_closes_only_peer(self) -> None:
        class FakeSocket:
            def shutdown(self, _how):
                pass

            def close(self):
                pass

        removed = []
        peer = daemon._Peer(  # type: ignore[arg-type]
            FakeSocket(), role="observer", inbound=queue.Queue(), on_close=removed.append
        )
        for index in range(daemon.MAX_CLIENT_QUEUE):
            self.assertTrue(peer.send({"type": "telemetry", "event": str(index)}))
        self.assertFalse(peer.send({"type": "telemetry", "event": "overflow"}))
        self.assertTrue(peer.closed.is_set())
        self.assertEqual(removed, [peer])

        inbound = daemon._FairInboundQueue()
        inbound_removed = []
        inbound_peer = daemon._Peer(  # type: ignore[arg-type]
            FakeSocket(),
            role="observer",
            inbound=inbound,
            on_close=inbound_removed.append,
        )
        for index in range(daemon.MAX_PEER_INBOUND_QUEUE):
            self.assertTrue(inbound_peer._queue_inbound({"type": "ping", "n": index}))
        self.assertFalse(inbound_peer._queue_inbound({"type": "ping"}))
        self.assertEqual(inbound.qsize(), daemon.MAX_PEER_INBOUND_QUEUE)
        self.assertEqual(inbound_removed, [inbound_peer])

        class BlockingSocket(FakeSocket):
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def sendall(self, _value):
                self.started.set()
                self.release.wait(1)

        blocking_socket = BlockingSocket()
        writing_peer = daemon._Peer(  # type: ignore[arg-type]
            blocking_socket,
            role="observer",
            inbound=queue.Queue(),
            on_close=lambda _peer: None,
        )
        writing_peer.writer.start()
        self.assertTrue(writing_peer.send({"type": "telemetry", "event": "final"}))
        self.assertTrue(blocking_socket.started.wait(1))
        self.assertTrue(writing_peer.outbound.empty())
        self.assertFalse(writing_peer.output_idle())

        endpoints = daemon.LocalEndpoints.__new__(daemon.LocalEndpoints)
        endpoints._lock = threading.Lock()
        endpoints._controller = None
        endpoints._observers = {writing_peer}
        settled = threading.Event()
        settle_thread = threading.Thread(
            target=lambda: (endpoints.settle(timeout=0.5), settled.set())
        )
        settle_thread.start()
        time.sleep(0.02)
        self.assertFalse(settled.is_set())
        blocking_socket.release.set()
        self.assertTrue(settled.wait(1))
        self.assertTrue(writing_peer.output_idle())
        writing_peer.close()
        writing_peer.writer.join(1)

        class FairPeer:
            def __init__(self, role) -> None:
                self.role = role
                self.closed = threading.Event()
                self.sent = []

            def send(self, value):
                self.sent.append(value)
                return True

        observer = FairPeer("observer")
        controller = FairPeer("controller")
        fair = daemon._FairInboundQueue()
        for _ in range(daemon.MAX_LOCAL_DRAIN + 2):
            fair.put((observer, {"type": "ping"}))  # type: ignore[arg-type]
        fair.put((controller, {"type": "ping"}))  # type: ignore[arg-type]

        class FairEndpoints:
            inbound = fair

            @staticmethod
            def is_current(_peer):
                return True

        service = daemon.InteractiveRelayDaemon.__new__(daemon.InteractiveRelayDaemon)
        service.endpoints = FairEndpoints()
        service._drain_local()
        self.assertEqual(controller.sent, [{"type": "pong"}])
        self.assertEqual(fair.qsize(), 3)

    def test_cli_commands_map_to_typed_messages(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.values = []
                self.approval_binding = {
                    "turn_id": 1,
                    "request_id": 2,
                    "corr_id": 3,
                    "tool": "send_message",
                    "arguments_sha256": "a" * 64,
                    "nonce": "approval-nonce",
                }

            def send(self, value):
                self.values.append(value)

        connection = FakeConnection()
        bound_once = {
            "type": "approval",
            "decision": "session",
            **connection.approval_binding,
        }
        bound_deny = {
            "type": "approval",
            "decision": "deny",
            **connection.approval_binding,
        }
        self.assertEqual(cli._send_command(connection, "hello"), "turn")  # type: ignore[arg-type]
        self.assertEqual(cli._send_command(connection, "/approve session"), "approval")  # type: ignore[arg-type]
        self.assertEqual(cli._send_command(connection, "/deny"), "approval")  # type: ignore[arg-type]
        self.assertEqual(cli._send_command(connection, "/status"), "control")  # type: ignore[arg-type]
        self.assertEqual(cli._send_command(connection, "/quit"), "quit")  # type: ignore[arg-type]
        self.assertEqual(
            cli._send_command(connection, "", approval_pending=True),  # type: ignore[arg-type]
            "approval",
        )
        self.assertEqual(
            connection.values,
            [
                {"type": "user_message", "content": "hello"},
                bound_once,
                bound_deny,
                {"type": "command", "command": "status"},
                {"type": "session_close"},
                bound_deny,
            ],
        )

    def test_observer_renders_high_signal_fields(self) -> None:
        output = io.StringIO()
        observe.render_event(
            {
                "type": "telemetry",
                "tick": 1841,
                "pid": 4,
                "state": "WAITING_LLM",
                "event": "llm_request",
                "tool_id": 1002,
                "corr_id": 12,
                "status": "ok",
                "context_seq": 37,
                "source_pid": 4,
                "target_pid": 9,
                "sched_budget_used": 14,
                "sched_vruntime": 21,
                "provenance": "UNTRUSTED_TOOL_OUTPUT",
            },
            output,
            json_events=False,
        )
        value = output.getvalue()
        for expected in (
            "1841",
            "WAITING_LLM",
            "1002",
            "4->9",
            "14",
            "21",
            "37",
            "UNTRUSTED",
        ):
            self.assertIn(expected, value)


if __name__ == "__main__":
    unittest.main()
