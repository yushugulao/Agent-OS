#!/usr/bin/env python3
"""Unit tests for the transport-neutral MCP/A2A Task gateway."""

from __future__ import annotations

import copy
import hashlib
import itertools
import unittest
from datetime import datetime, timezone

import agent_task_transport as transport
import mcp_a2a_gateway as gateway


class McpA2AGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = transport.TaskChannelIssuer("main-issuer", 5, 7, 9)
        self.adapter = transport.InMemoryTaskChannelTransport(
            self.issuer,
            handlers={
                1: lambda request: {
                    "content": [{"type": "text", "text": request.payload["query"]}],
                    "structuredContent": {"echo": request.payload["query"]},
                }
            },
        )
        self.store = gateway.InMemoryTaskStore()
        counter = itertools.count(1)
        self.id_factory = lambda: f"opaque{next(counter):026d}"
        self.schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }
        self.output_schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"echo": {"type": "string"}},
                    "required": ["echo"],
                    "additionalProperties": False,
                },
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            ]
        }
        self.tools = (
            gateway.ToolManifest(
                name="echo",
                tool_id=1,
                contract_node_id=0,
                input_schema=self.schema,
                output_schema=self.output_schema,
                kernel_manifest_digest="1" * 64,
                task_mode="sync",
                deadline_ns=1_000_000,
            ),
            gateway.ToolManifest(
                name="research",
                tool_id=2,
                contract_node_id=1,
                input_schema=self.schema,
                kernel_manifest_digest="2" * 64,
                task_mode="task",
                deadline_ns=2_000_000,
            ),
        )
        policy = gateway.StaticIssuerPolicy(
            {"https://issuer.example": frozenset(("tenant-a",))}
        )
        self.service = gateway.McpA2AGateway(
            transport=self.adapter,
            channel_issuer=self.issuer,
            contract_generation=17,
            tools=self.tools,
            identity_validator=policy,
            task_store=self.store,
            id_factory=self.id_factory,
            clock=lambda: datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
            a2a_tenant="tenant-a",
        )
        self.alice = gateway.GatewayPrincipal(
            "https://issuer.example", "tenant-a", "alice"
        )
        self.bob = gateway.GatewayPrincipal(
            "https://issuer.example", "tenant-a", "bob"
        )

    @staticmethod
    def mcp_meta(*, tasks: bool = False) -> dict[str, object]:
        extensions: dict[str, object] = {}
        if tasks:
            extensions[gateway.MCP_TASKS_EXTENSION] = {}
        return {
            "io.modelcontextprotocol/protocolVersion": gateway.MCP_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {"extensions": extensions},
        }

    def mcp_envelope(
        self,
        method: str,
        params: dict[str, object],
        *,
        name: str | None = None,
        version: str = gateway.MCP_PROTOCOL_VERSION,
    ) -> gateway.McpRequestEnvelope:
        body_params = copy.deepcopy(params)
        body_params.setdefault("_meta", self.mcp_meta())
        return gateway.McpRequestEnvelope(
            body={"jsonrpc": "2.0", "id": 10, "method": method, "params": body_params},
            protocol_version=version,
            method_header=method,
            name_header=name,
        )

    def create_mcp_task(self) -> tuple[str, transport.TaskBinding]:
        params = {
            "name": "research",
            "arguments": {"query": "topic"},
            "_meta": self.mcp_meta(tasks=True),
        }
        response = self.service.handle_mcp(
            self.mcp_envelope("tools/call", params, name="research"), self.alice
        )
        result = response["result"]
        self.assertEqual(result["resultType"], "task")
        task_id = result["taskId"]
        stored = self.store.get(task_id)
        self.assertIsNotNone(stored)
        return task_id, stored.binding  # type: ignore[union-attr]

    @staticmethod
    def a2a_message(
        *, task_id: str | None = None, legacy: bool = False
    ) -> dict[str, object]:
        part: dict[str, object] = {"text": "investigate", "mediaType": "text/plain"}
        if legacy:
            part["kind"] = "text"
        message: dict[str, object] = {
            "messageId": "message-1" if task_id is None else "message-2",
            "role": "ROLE_USER",
            "parts": [part],
            "metadata": {"skill": "research"},
        }
        if task_id is not None:
            message["taskId"] = task_id
        return message

    def a2a_envelope(
        self,
        operation: str,
        body: dict[str, object],
        *,
        version: str = gateway.A2A_PROTOCOL_VERSION,
        tenant: str = "tenant-a",
    ) -> gateway.A2ARequestEnvelope:
        return gateway.A2ARequestEnvelope(body, version, operation, tenant)

    def test_remote_schema_digest_is_stable_and_distinct_from_kernel_manifest(self) -> None:
        reordered = {
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
            "type": "object",
        }
        expected = hashlib.sha256(gateway.canonical_json_bytes(self.schema)).hexdigest()
        self.assertEqual(gateway.canonical_schema_digest(reordered), expected)
        listed = self.service.mcp_tools_list(self.alice)
        echo = next(item for item in listed["tools"] if item["name"] == "echo")
        self.assertEqual(echo["_meta"]["io.agentos/remoteSchemaSha256"], expected)
        self.assertEqual(echo["_meta"]["io.agentos/kernelManifestSha256"], "1" * 64)
        self.assertNotEqual(expected, "1" * 64)

    def test_server_discovery_advertises_versions_tools_identity_and_cache_hints(self) -> None:
        response = self.service.handle_mcp(
            self.mcp_envelope("server/discover", {}), self.alice
        )
        result = response["result"]
        self.assertEqual(result["supportedVersions"], [gateway.MCP_PROTOCOL_VERSION])
        self.assertNotIn("protocolVersion", result)
        self.assertEqual(result["capabilities"]["tools"], {})
        self.assertIn(
            gateway.MCP_TASKS_EXTENSION,
            result["capabilities"]["extensions"],
        )
        self.assertEqual(result["ttlMs"], 30_000)
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual(
            result["_meta"][gateway.MCP_SERVER_INFO_METADATA],
            {"name": "agentos-mcp-a2a-gateway", "version": "1.0"},
        )

    def test_bounded_recursive_json_schema_subset(self) -> None:
        schema = {
            "$defs": {
                "code": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 4,
                    "pattern": "^[A-Z]+$",
                }
            },
            "type": "object",
            "properties": {
                "mode": {"enum": ["fast", "safe"]},
                "fixed": {"const": 3},
                "records": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"$ref": "#/$defs/code"},
                            "score": {
                                "type": "number",
                                "minimum": 0,
                                "exclusiveMaximum": 10,
                                "multipleOf": 0.5,
                            },
                        },
                        "required": ["code", "score"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["mode", "fixed", "records"],
            "allOf": [{"properties": {"fixed": {"type": "integer"}}}],
            "anyOf": [
                {"properties": {"mode": {"const": "fast"}}},
                {"properties": {"mode": {"const": "safe"}}},
            ],
            "oneOf": [{"required": ["fixed"]}, {"required": ["alternate"]}],
            "not": {"required": ["forbidden"]},
            "additionalProperties": False,
        }
        valid = {
            "mode": "fast",
            "fixed": 3,
            "records": [{"code": "AB", "score": 2.5}],
        }
        gateway._default_schema_validator(schema, valid)

        invalid_pattern = copy.deepcopy(valid)
        invalid_pattern["records"][0]["code"] = "lower"
        with self.assertRaises(gateway.InvalidProtocolRequestError):
            gateway._default_schema_validator(schema, invalid_pattern)
        invalid_bound = copy.deepcopy(valid)
        invalid_bound["records"][0]["score"] = 10
        with self.assertRaises(gateway.InvalidProtocolRequestError):
            gateway._default_schema_validator(schema, invalid_bound)
        duplicates = copy.deepcopy(valid)
        duplicates["records"].append(copy.deepcopy(duplicates["records"][0]))
        with self.assertRaises(gateway.InvalidProtocolRequestError):
            gateway._default_schema_validator(schema, duplicates)

        recursive_schema = {
            "$defs": {
                "node": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/$defs/node"}},
                    "additionalProperties": False,
                }
            },
            "$ref": "#/$defs/node",
        }
        gateway._default_schema_validator(
            recursive_schema, {"next": {"next": {}}}
        )
        rejected_combinators = (
            ({"allOf": [{"required": ["a"]}, {"required": ["b"]}]}, {"a": 1}),
            ({"anyOf": [{"required": ["a"]}, {"required": ["b"]}]}, {}),
            ({"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}, {"a": 1, "b": 2}),
            ({"not": {"required": ["a"]}}, {"a": 1}),
        )
        for branch_schema, branch_value in rejected_combinators:
            with self.assertRaises(gateway.InvalidProtocolRequestError):
                gateway._default_schema_validator(branch_schema, branch_value)

        deep: dict[str, object] = {}
        cursor = deep
        for _ in range(70):
            child: dict[str, object] = {}
            cursor["child"] = child
            cursor = child
        with self.assertRaises(gateway.InvalidProtocolRequestError):
            gateway._default_schema_validator({"type": "object"}, deep)

    def test_tool_manifest_rejects_unimplemented_schema_keywords_before_listing(self) -> None:
        unsupported_schemas = {
            "if": {
                "type": "object",
                "if": {"required": ["kind"]},
            },
            "then": {
                "type": "object",
                "then": {"required": ["value"]},
            },
            "dependentRequired": {
                "type": "object",
                "dependentRequired": {"card": ["billingAddress"]},
            },
            "unevaluatedProperties": {
                "type": "object",
                "unevaluatedProperties": False,
            },
        }
        for keyword, schema in unsupported_schemas.items():
            with self.subTest(keyword=keyword), self.assertRaisesRegex(
                ValueError, keyword
            ):
                gateway.ToolManifest(
                    name="unsupported",
                    tool_id=3,
                    contract_node_id=3,
                    input_schema=schema,
                    kernel_manifest_digest="3" * 64,
                )

        external_ref = {
            "type": "object",
            "properties": {
                "value": {"$ref": "https://example.invalid/schema.json"}
            },
        }
        with self.assertRaisesRegex(ValueError, "local JSON Pointer"):
            gateway.ToolManifest(
                name="external_ref",
                tool_id=3,
                contract_node_id=3,
                input_schema=external_ref,
                kernel_manifest_digest="3" * 64,
            )

    def test_sync_input_failure_is_a_call_tool_result_without_submission(self) -> None:
        response = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": 7}},
                name="echo",
            ),
            self.alice,
        )
        self.assertNotIn("error", response)
        self.assertTrue(response["result"]["isError"])
        self.assertIsInstance(response["result"]["content"], list)
        self.assertIn("error", response["result"]["structuredContent"])

        malformed = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": []},
                name="echo",
            ),
            self.alice,
        )
        self.assertEqual(malformed["error"]["code"], -32602)
        self.assertEqual(self.adapter.submitted_requests, 0)

    def test_task_mode_input_failure_is_model_visible_without_submission(self) -> None:
        response = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {
                    "name": "research",
                    "arguments": {"query": 7},
                    "_meta": self.mcp_meta(tasks=True),
                },
                name="research",
            ),
            self.alice,
        )
        self.assertNotIn("error", response)
        self.assertEqual(response["result"]["resultType"], "complete")
        self.assertTrue(response["result"]["isError"])
        self.assertIsInstance(response["result"]["content"], list)
        self.assertIn("error", response["result"]["structuredContent"])
        self.assertEqual(self.adapter.submitted_requests, 0)
        self.assertEqual(self.store.list_owner(self.alice.owner_key), ())

    def test_sync_failures_and_reserved_mapping_fields_are_normalized(self) -> None:
        self.adapter._handlers[1] = lambda request: {
            "resultType": "task",
            "content": [{"type": "text", "text": "ok"}],
            "structuredContent": {"echo": request.payload["query"]},
            "_meta": {
                gateway.AGENTOS_TASK_METADATA: {"requestId": 999},
                gateway.MCP_SERVER_INFO_METADATA: {"name": "forged", "version": "0"},
                "example/custom": True,
            },
        }
        normalized = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": "hello"}},
                name="echo",
            ),
            self.alice,
        )["result"]
        self.assertEqual(normalized["resultType"], "complete")
        self.assertEqual(normalized["_meta"][gateway.AGENTOS_TASK_METADATA]["requestId"], 1)
        self.assertEqual(
            normalized["_meta"][gateway.MCP_SERVER_INFO_METADATA]["name"],
            "agentos-mcp-a2a-gateway",
        )
        self.assertTrue(normalized["_meta"]["example/custom"])

        self.adapter._handlers[1] = lambda request: {
            "content": [{"type": "text", "text": "array output"}],
            "structuredContent": [request.payload["query"]],
        }
        unrestricted_output = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": "hello"}},
                name="echo",
            ),
            self.alice,
        )["result"]
        self.assertFalse(unrestricted_output["isError"])
        self.assertEqual(unrestricted_output["structuredContent"], ["hello"])

        self.adapter._handlers[1] = lambda request: {
            "content": [{"type": "text", "text": "bad"}],
            "structuredContent": {"echo": 7},
        }
        invalid_output = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": "hello"}},
                name="echo",
            ),
            self.alice,
        )["result"]
        self.assertTrue(invalid_output["isError"])
        self.assertEqual(
            invalid_output["structuredContent"]["error"]["reason"],
            "INVALID_TOOL_OUTPUT",
        )

        self.adapter._handlers[1] = lambda request: {
            "content": [{"type": "text", "text": "missing structured output"}],
        }
        missing_output = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": "hello"}},
                name="echo",
            ),
            self.alice,
        )["result"]
        self.assertTrue(missing_output["isError"])
        self.assertEqual(
            missing_output["structuredContent"]["error"]["reason"],
            "INVALID_TOOL_OUTPUT",
        )

        self.adapter._handlers[1] = lambda request: transport.TaskChannelOutcome(
            status=transport.TaskStatus.FAILED,
            error={"message": "business rule rejected the call"},
        )
        failed = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call",
                {"name": "echo", "arguments": {"query": "hello"}},
                name="echo",
            ),
            self.alice,
        )
        self.assertNotIn("error", failed)
        self.assertTrue(failed["result"]["isError"])
        self.assertEqual(
            failed["result"]["structuredContent"]["error"]["reason"],
            "TOOL_EXECUTION_FAILED",
        )

    def test_tools_call_uses_frozen_authority_fields_and_sync_result(self) -> None:
        params = {
            "name": "echo",
            "arguments": {"query": "hello"},
            "contractNodeId": 99,
            "toolId": 65535,
            "deadlineNs": 9,
            "_meta": self.mcp_meta(),
        }
        response = self.service.handle_mcp(
            self.mcp_envelope("tools/call", params, name="echo"), self.alice
        )
        self.assertEqual(response["result"]["structuredContent"], {"echo": "hello"})
        self.assertEqual(self.adapter.submitted_requests, 1)
        binding = transport.TaskBinding(5, 7, 17, 9, 1)
        accepted = self.adapter.accepted_request(binding)
        self.assertEqual(accepted.tool_id, 1)
        self.assertEqual(accepted.contract_node_id, 0)
        self.assertEqual(accepted.contract_generation, 17)
        self.assertEqual(accepted.attempt_id, 1)
        self.assertEqual(accepted.deadline_ns, 1_000_000)
        self.assertEqual(accepted.schema_digest, "1" * 64)

    def test_protocol_and_capability_failures_have_zero_side_effects(self) -> None:
        async_params = {
            "name": "research",
            "arguments": {"query": "topic"},
            "_meta": self.mcp_meta(tasks=False),
        }
        missing = self.service.handle_mcp(
            self.mcp_envelope("tools/call", async_params, name="research"), self.alice
        )
        self.assertEqual(missing["error"]["code"], -32021)

        bad_version = self.service.handle_mcp(
            self.mcp_envelope(
                "tools/call", async_params, name="research", version="2025-11-25"
            ),
            self.alice,
        )
        self.assertEqual(bad_version["error"]["code"], -32022)

        wrong_header = gateway.McpRequestEnvelope(
            body={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": async_params,
            },
            protocol_version=gateway.MCP_PROTOCOL_VERSION,
            method_header="tools/list",
            name_header="research",
        )
        mismatch = self.service.handle_mcp(wrong_header, self.alice)
        self.assertEqual(mismatch["error"]["code"], -32020)
        self.assertEqual(self.adapter.submitted_requests, 0)

    def test_task_mapping_is_non_enumerable_across_callers_and_issuer_checked(self) -> None:
        task_id, _ = self.create_mcp_task()
        with self.assertRaises(gateway.TaskNotFoundError):
            self.service.mcp_tasks_get(self.bob, task_id)
        with self.assertRaises(gateway.TaskNotFoundError):
            self.service.mcp_tasks_get(self.bob, "task_unknown")
        mallory = gateway.GatewayPrincipal("https://evil.example", "tenant-a", "mallory")
        with self.assertRaisesRegex(gateway.UnauthorizedGatewayError, "issuer"):
            self.service.mcp_tasks_get(mallory, task_id)

    def test_task_update_notifications_and_terminal_state_order(self) -> None:
        task_id, binding = self.create_mcp_task()
        self.adapter.publish(
            binding,
            transport.TaskStatus.INPUT_REQUIRED,
            input_requests={"approval": {"method": "elicitation"}},
        )
        waiting = self.service.mcp_tasks_get(self.alice, task_id)
        self.assertEqual(waiting["status"], "input_required")
        self.service.mcp_tasks_update(self.alice, task_id, {"unknown": True})
        self.assertEqual(self.service.mcp_tasks_get(self.alice, task_id)["status"], "input_required")
        self.service.mcp_tasks_update(
            self.alice, task_id, {"approval": {"approved": True}}
        )
        self.adapter.publish(
            binding,
            transport.TaskStatus.COMPLETED,
            result={"content": [{"type": "text", "text": "done"}]},
            provenance=("UNTRUSTED_TOOL_OUTPUT",),
        )
        completed = self.service.mcp_tasks_get(self.alice, task_id)
        self.assertEqual(completed["status"], "completed")
        metadata = completed["_meta"][gateway.AGENTOS_TASK_METADATA]
        self.assertGreater(metadata["contextSequence"], 0)
        self.assertGreater(metadata["evidenceTicket"], 0)
        self.assertEqual(metadata["provenance"], ["UNTRUSTED_TOOL_OUTPUT"])

        notifications = self.service.mcp_task_notifications(
            self.alice, task_ids=(task_id,)
        )
        statuses = [item["params"]["status"] for item in notifications]
        self.assertEqual(
            statuses,
            ["working", "input_required", "working", "completed"],
        )
        sequences = [
            item["params"]["_meta"][gateway.AGENTOS_TASK_METADATA]["contextSequence"]
            for item in notifications
        ]
        self.assertEqual(sequences, sorted(sequences))

    def test_duplicate_mcp_cancel_is_exactly_once(self) -> None:
        task_id, _ = self.create_mcp_task()
        self.assertEqual(self.service.mcp_tasks_cancel(self.alice, task_id), {"resultType": "complete"})
        self.assertEqual(self.service.mcp_tasks_cancel(self.alice, task_id), {"resultType": "complete"})
        self.assertEqual(self.adapter.cancel_transitions, 1)
        self.assertEqual(self.service.mcp_tasks_get(self.alice, task_id)["status"], "cancelled")

    def test_a2a_v1_context_message_artifact_stream_and_follow_up(self) -> None:
        created = self.service.a2a_send_message(
            self.a2a_envelope("SendMessage", {"message": self.a2a_message()}), self.alice
        )
        self.assertEqual(created["status"]["state"], "TASK_STATE_WORKING")
        self.assertTrue(created["contextId"].startswith("context_"))
        task_id = created["id"]
        stored = self.store.get(task_id)
        self.assertIsNotNone(stored)
        binding = stored.binding  # type: ignore[union-attr]
        artifact_event = self.adapter.publish_artifact(
            binding,
            {
                "artifactId": "artifact-remote",
                "name": "partial",
                "parts": [{"data": {"rows": 1}, "mediaType": "application/json"}],
            },
            artifact_index=0,
            last_chunk=True,
            provenance=("UNTRUSTED_TOOL_OUTPUT",),
        )
        stream = self.service.a2a_stream(
            self.a2a_envelope("SubscribeToTask", {"id": task_id}),
            self.alice,
            include_snapshot=True,
        )
        self.assertIn("task", stream[0])
        wrappers = [next(iter(item)) for item in stream[1:]]
        self.assertEqual(wrappers, ["statusUpdate", "artifactUpdate"])
        self.assertNotIn("kind", stream[-1])
        self.assertNotIn("final", stream[-1])
        event_meta = stream[-1]["artifactUpdate"]["metadata"][gateway.AGENTOS_TASK_METADATA]
        self.assertEqual(event_meta["eventSequence"], artifact_event.sequence)

        self.adapter.publish(
            binding,
            transport.TaskStatus.INPUT_REQUIRED,
            input_requests={"answer": {"type": "message"}},
        )
        followed = self.service.a2a_send_message(
            self.a2a_envelope(
                "SendMessage", {"message": self.a2a_message(task_id=task_id)}
            ),
            self.alice,
        )
        self.assertEqual(followed["id"], task_id)
        self.assertEqual(followed["contextId"], created["contextId"])
        self.assertEqual(followed["status"]["state"], "TASK_STATE_WORKING")
        self.assertEqual(len(followed["history"]), 2)

    def test_a2a_cancel_is_idempotent_and_version_tenant_fail_closed(self) -> None:
        baseline = self.adapter.submitted_requests
        bad_version = self.a2a_envelope(
            "SendMessage",
            {"message": self.a2a_message()},
            version="0.3",
        )
        with self.assertRaises(gateway.UnsupportedProtocolVersionError):
            self.service.a2a_send_message(bad_version, self.alice)
        with self.assertRaises(gateway.UnauthorizedGatewayError):
            self.service.a2a_send_message(
                self.a2a_envelope(
                    "SendMessage", {"message": self.a2a_message()}, tenant="tenant-b"
                ),
                self.alice,
            )
        with self.assertRaisesRegex(gateway.InvalidProtocolRequestError, "member-based"):
            self.service.a2a_send_message(
                self.a2a_envelope(
                    "SendMessage", {"message": self.a2a_message(legacy=True)}
                ),
                self.alice,
            )
        self.assertEqual(self.adapter.submitted_requests, baseline)

        created = self.service.a2a_send_message(
            self.a2a_envelope("SendMessage", {"message": self.a2a_message()}), self.alice
        )
        cancel = self.a2a_envelope("CancelTask", {"id": created["id"]})
        first = self.service.a2a_cancel_task(cancel, self.alice)
        second = self.service.a2a_cancel_task(cancel, self.alice)
        self.assertEqual(first["status"]["state"], "TASK_STATE_CANCELED")
        self.assertEqual(second["status"]["state"], "TASK_STATE_CANCELED")
        self.assertEqual(self.adapter.cancel_transitions, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
