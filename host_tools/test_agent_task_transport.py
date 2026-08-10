#!/usr/bin/env python3
"""Unit tests for the transport-neutral Agent Task Channel adapter."""

from __future__ import annotations

import concurrent.futures
import threading
import unittest

import agent_task_transport as transport


DIGEST = "a" * 64


class AgentTaskTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = transport.TaskChannelIssuer(
            issuer_id="agent-main-thread",
            lifecycle_id=7,
            lifecycle_generation=11,
            channel_generation=13,
        )

    def request(self, **changes: object) -> transport.TaskChannelRequest:
        values: dict[str, object] = {
            "tool_id": 3,
            "schema_digest": DIGEST,
            "contract_node_id": 0,
            "contract_generation": 17,
            "attempt_id": 1,
            "payload": {"query": "alpha"},
            "submission_key": "submission-1",
            "provenance": ("CROSS_AGENT_DATA",),
        }
        values.update(changes)
        return transport.TaskChannelRequest(**values)  # type: ignore[arg-type]

    def test_descriptor_validation_matches_current_kernel_widths(self) -> None:
        self.assertEqual(self.request().contract_node_id, 0)
        self.assertEqual(self.request(tool_id=0xFFFF).tool_id, 0xFFFF)
        with self.assertRaisesRegex(ValueError, "uint16"):
            self.request(tool_id=0x10000)
        with self.assertRaisesRegex(ValueError, "uint32"):
            self.request(contract_node_id=-1)
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            self.request(schema_digest="A" * 64)
        with self.assertRaisesRegex(ValueError, "positive uint32"):
            transport.TaskChannelIssuer("issuer", 0x1_0000_0000, 1, 1)

    def test_submit_snapshots_payload_and_deduplicates_identical_bytes(self) -> None:
        observed: list[object] = []

        def handler(request: transport.TaskChannelRequest) -> object:
            observed.append(request.payload)
            return {"structuredContent": {"answer": 42}}

        adapter = transport.InMemoryTaskChannelTransport(
            self.issuer, handlers={3: handler}
        )
        payload = {"query": ["alpha"]}
        first = adapter.submit(self.issuer, self.request(payload=payload))
        payload["query"].append("mutated")
        self.assertEqual(observed, [{"query": ["alpha"]}])
        self.assertEqual(first.snapshot.status, transport.TaskStatus.COMPLETED)
        self.assertIsNotNone(first.snapshot.result_handle)

        duplicate = adapter.submit(
            self.issuer, self.request(payload={"query": ["alpha"]})
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.binding, first.binding)
        self.assertEqual(adapter.submitted_requests, 1)
        self.assertEqual(adapter.handler_invocations, 1)

        with self.assertRaisesRegex(transport.DuplicateRequestError, "different"):
            adapter.submit(self.issuer, self.request(payload={"query": "other"}))

    def test_wrong_issuer_and_stale_generation_fail_closed(self) -> None:
        adapter = transport.InMemoryTaskChannelTransport(self.issuer)
        wrong = transport.TaskChannelIssuer("other-thread", 7, 11, 13)
        with self.assertRaisesRegex(transport.InvalidIssuerError, "wrong issuer"):
            adapter.submit(wrong, self.request())
        submitted = adapter.submit(self.issuer, self.request())
        stale = transport.TaskBinding(7, 11, 17, 14, submitted.binding.request_id)
        with self.assertRaisesRegex(transport.UnknownRequestError, "stale"):
            adapter.snapshot(self.issuer, stale)

    def test_input_update_and_terminal_state_invariants(self) -> None:
        adapter = transport.InMemoryTaskChannelTransport(self.issuer)
        submitted = adapter.submit(self.issuer, self.request())
        waiting = adapter.publish(
            submitted.binding,
            transport.TaskStatus.INPUT_REQUIRED,
            input_requests={"approval": {"method": "elicitation"}},
        )
        self.assertEqual(waiting.status, transport.TaskStatus.INPUT_REQUIRED)
        unchanged = adapter.update(self.issuer, submitted.binding, {"unknown": True})
        self.assertEqual(unchanged.status, transport.TaskStatus.INPUT_REQUIRED)
        working = adapter.update(
            self.issuer, submitted.binding, {"approval": {"approved": True}}
        )
        self.assertEqual(working.status, transport.TaskStatus.WORKING)
        completed = adapter.publish(
            submitted.binding, transport.TaskStatus.COMPLETED, result={"ok": True}
        )
        self.assertEqual(completed.status, transport.TaskStatus.COMPLETED)
        with self.assertRaisesRegex(transport.InvalidTaskTransitionError, "terminal"):
            adapter.publish(submitted.binding, transport.TaskStatus.WORKING)

    def test_partial_input_is_retained_and_full_input_runs_continuation(self) -> None:
        observed: list[tuple[object, dict[str, object]]] = []

        def handler(
            request: transport.TaskChannelRequest,
        ) -> transport.TaskChannelOutcome:
            return transport.TaskChannelOutcome(
                status=transport.TaskStatus.INPUT_REQUIRED,
                status_message="need both inputs",
                input_requests={
                    "approval": {"method": "elicitation"},
                    "ticket": {"type": "string"},
                },
            )

        def continuation(
            request: transport.TaskChannelRequest,
            responses: dict[str, object],
        ) -> transport.TaskChannelOutcome:
            observed.append((request.payload, responses))
            return transport.TaskChannelOutcome(
                status=transport.TaskStatus.COMPLETED,
                result={"accepted": sorted(responses)},
            )

        adapter = transport.InMemoryTaskChannelTransport(
            self.issuer,
            handlers={3: handler},
            continuation_handlers={3: continuation},
        )
        submitted = adapter.submit(self.issuer, self.request())
        self.assertEqual(submitted.snapshot.status, transport.TaskStatus.INPUT_REQUIRED)

        partial_value = {"approved": True}
        partial = adapter.update(
            self.issuer, submitted.binding, {"approval": partial_value}
        )
        partial_value["approved"] = False
        self.assertEqual(partial.status, transport.TaskStatus.INPUT_REQUIRED)
        self.assertEqual(partial.input_requests, {"ticket": {"type": "string"}})
        self.assertEqual(observed, [])

        completed = adapter.update(
            self.issuer,
            submitted.binding,
            {"ticket": "T-7", "not-requested": "ignored"},
        )
        self.assertEqual(completed.status, transport.TaskStatus.COMPLETED)
        self.assertEqual(completed.result, {"accepted": ["approval", "ticket"]})
        self.assertEqual(
            observed,
            [
                (
                    {"query": "alpha"},
                    {"approval": {"approved": True}, "ticket": "T-7"},
                )
            ],
        )
        self.assertEqual(adapter.continuation_invocations, 1)
        self.assertEqual(
            [event.status for event in adapter.events(self.issuer, submitted.binding)],
            [
                transport.TaskStatus.WORKING,
                transport.TaskStatus.INPUT_REQUIRED,
                transport.TaskStatus.INPUT_REQUIRED,
                transport.TaskStatus.WORKING,
                transport.TaskStatus.COMPLETED,
            ],
        )

    def test_slow_handler_does_not_block_snapshot_or_cancel(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def handler(request: transport.TaskChannelRequest) -> object:
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release handler")
            return {"late": request.payload}

        adapter = transport.InMemoryTaskChannelTransport(
            self.issuer, handlers={3: handler}
        )
        binding = transport.TaskBinding(7, 11, 17, 13, 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            submitted_future = executor.submit(
                adapter.submit, self.issuer, self.request()
            )
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(
                adapter.snapshot(self.issuer, binding).status,
                transport.TaskStatus.WORKING,
            )
            cancelled = adapter.cancel(self.issuer, binding)
            self.assertEqual(cancelled.status, transport.TaskStatus.CANCELLED)
            release.set()
            submitted = submitted_future.result(timeout=2)

        self.assertEqual(submitted.snapshot.status, transport.TaskStatus.CANCELLED)
        terminal = [
            event
            for event in adapter.events(self.issuer, binding)
            if event.status in transport.TERMINAL_TASK_STATUSES
        ]
        self.assertEqual([event.status for event in terminal], [transport.TaskStatus.CANCELLED])

    def test_handler_and_continuation_exceptions_fail_terminally(self) -> None:
        def failing_handler(
            request: transport.TaskChannelRequest,
        ) -> transport.TaskChannelOutcome:
            raise RuntimeError(f"cannot handle {request.tool_id}")

        handler_adapter = transport.InMemoryTaskChannelTransport(
            self.issuer, handlers={3: failing_handler}
        )
        failed = handler_adapter.submit(self.issuer, self.request()).snapshot
        self.assertEqual(failed.status, transport.TaskStatus.FAILED)
        self.assertEqual(failed.error["code"], "HANDLER_EXCEPTION")  # type: ignore[index]
        self.assertEqual(failed.error["exceptionType"], "RuntimeError")  # type: ignore[index]

        def needs_input(
            request: transport.TaskChannelRequest,
        ) -> transport.TaskChannelOutcome:
            return transport.TaskChannelOutcome(
                status=transport.TaskStatus.INPUT_REQUIRED,
                input_requests={"answer": {"type": "string"}},
            )

        def failing_continuation(
            request: transport.TaskChannelRequest,
            responses: dict[str, object],
        ) -> object:
            raise ValueError(f"bad answer: {responses['answer']}")

        continuation_adapter = transport.InMemoryTaskChannelTransport(
            self.issuer,
            handlers={3: needs_input},
            continuation_handlers={3: failing_continuation},
            first_request_id=9,
        )
        waiting = continuation_adapter.submit(
            self.issuer, self.request(submission_key="continuation-failure")
        )
        continuation_failed = continuation_adapter.update(
            self.issuer, waiting.binding, {"answer": "no"}
        )
        self.assertEqual(continuation_failed.status, transport.TaskStatus.FAILED)
        self.assertEqual(
            continuation_failed.error["code"],  # type: ignore[index]
            "CONTINUATION_EXCEPTION",
        )

    def test_concurrent_cancel_has_exactly_one_terminal_transition(self) -> None:
        adapter = transport.InMemoryTaskChannelTransport(self.issuer)
        binding = adapter.submit(self.issuer, self.request()).binding
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(
                executor.map(
                    lambda _: adapter.cancel(self.issuer, binding),
                    range(32),
                )
            )
        self.assertTrue(
            all(item.status is transport.TaskStatus.CANCELLED for item in snapshots)
        )
        self.assertEqual(adapter.cancel_transitions, 1)
        events = adapter.events(self.issuer, binding)
        cancelled = [event for event in events if event.status is transport.TaskStatus.CANCELLED]
        self.assertEqual(len(cancelled), 1)

    def test_events_preserve_global_order_and_verifiable_metadata(self) -> None:
        adapter = transport.InMemoryTaskChannelTransport(self.issuer)
        binding = adapter.submit(self.issuer, self.request()).binding
        artifact = adapter.publish_artifact(
            binding,
            {"artifactId": "part-1", "parts": [{"text": "chunk"}]},
            artifact_index=0,
            last_chunk=True,
        )
        terminal = adapter.publish(
            binding, transport.TaskStatus.COMPLETED, result={"done": True}
        )
        events = adapter.events(self.issuer, binding)
        self.assertEqual([item.sequence for item in events], sorted(item.sequence for item in events))
        self.assertEqual(artifact.kind, transport.TaskEventKind.ARTIFACT)
        self.assertGreater(terminal.context_sequence, 0)
        self.assertGreater(terminal.evidence_ticket, 0)
        self.assertEqual(terminal.binding.lifecycle_generation, 11)
        self.assertEqual(terminal.binding.contract_generation, 17)
        replay = adapter.events(
            self.issuer, binding, after_sequence=artifact.sequence
        )
        self.assertEqual([item.status for item in replay], [transport.TaskStatus.COMPLETED])


if __name__ == "__main__":
    unittest.main(verbosity=2)
