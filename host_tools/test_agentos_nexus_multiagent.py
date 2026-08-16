#!/usr/bin/env python3
"""Focused tests for the task-independent Nexus multi-Agent Harness."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_multiagent as nexus


class _RootModel:
    def __call__(self, projection):
        completed = [
            row for row in projection["context"]
            if row.get("kind") == "task_completed" and row.get("status") == "ok"
        ]
        if not completed:
            return {
                "type": "delegate",
                "objective": "Inspect the supplied objective and return one concise report.",
                "capabilities": ["READ_CONTEXT", "SHARE_ARTIFACT"],
                "tools": [],
                "system_prompt": "Return evidence from the assigned objective.",
                "label": "dynamic-child",
                "resource_budget": 4,
                "read_budget": 8192,
                "expected_result_kind": "subtask_report",
            }
        return {"type": "final", "content": "accepted a sealed child report"}


class _ChildModel:
    def __call__(self, projection):
        self.assert_projection(projection)
        return {"type": "final", "content": "child evidence"}

    @staticmethod
    def assert_projection(projection):
        assert projection["task"]["objective"].startswith("Inspect")


class _ParallelRootModel:
    def __init__(self):
        self.delegated = 0

    def __call__(self, projection):
        completed = {
            int(row["task_id"])
            for row in projection["context"]
            if row.get("kind") == "task_completed" and row.get("status") == "ok"
        }
        if self.delegated < 2:
            self.delegated += 1
            return {
                "type": "delegate",
                "objective": f"Inspect parallel unit {self.delegated}.",
                "capabilities": ["READ_CONTEXT", "SHARE_ARTIFACT"],
                "tools": [],
                "system_prompt": "Return one sealed report.",
                "label": f"parallel-child-{self.delegated}",
                "resource_budget": 4,
                "read_budget": 8192,
                "expected_result_kind": "subtask_report",
                "continue_parent": True,
            }
        if len(completed) < 2:
            return {"type": "wait"}
        return {"type": "final", "content": "accepted two parallel reports"}


class _RepairProvider:
    def __init__(self):
        self.requests = []

    def complete(self, request, *, deadline_monotonic=None):
        self.requests.append((dict(request), deadline_monotonic))
        if len(self.requests) == 1:
            raise nexus.relay.ProviderError(
                "MULTIPLE_TOOL_CALLS", "parallel calls", retryable=True
            )
        if len(self.requests) == 2:
            return nexus.relay.ModelReply(
                "final", content="The next action is `search_files`."
            )
        return nexus.relay.ModelReply(
            "tool_use", tool="search_files", arguments={"query": "main", "path": "user/src"}
        )


class _OversizedFinalProvider:
    def __init__(self):
        self.requests = []

    def complete(self, request, *, deadline_monotonic=None):
        self.requests.append((dict(request), deadline_monotonic))
        if len(self.requests) == 1:
            raise nexus.relay.ProviderError(
                "BAD_PROVIDER_RESPONSE",
                "model final content exceeds 2048 UTF-8 bytes",
            )
        if len(self.requests) == 2:
            return nexus.relay.ModelReply("final", content="search_files")
        return nexus.relay.ModelReply(
            "tool_use", tool="search_files", arguments={"query": "main", "path": "user/src"}
        )


class MultiAgentHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nexus-multi-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _policy(self):
        return nexus.WorkflowPolicy(
            frozenset(("READ_CONTEXT", "ORCHESTRATE", "SHARE_ARTIFACT")),
            frozenset(),
            resource_budget=16,
            artifact_count_limit=32,
            artifact_bytes_limit=256 * 1024,
            artifact_read_limit=256 * 1024,
        )

    def test_dynamic_child_uses_common_loop_and_settled_artifact(self):
        def factory(config):
            return _ChildModel()

        harness = nexus.NexusHarness(
            self.root,
            "Perform a generic delegated task",
            self._policy(),
            model_factory=factory,
        )
        try:
            root_config = nexus.AgentConfig(
                self._policy().capabilities,
                frozenset(),
                "Plan dynamically.",
                resource_budget=16,
                artifact_read_limit=256 * 1024,
            )
            root = harness.spawn(root_config, _RootModel(), "configured-root")
            root_task = harness.submit_root(root)
            deadline = time.monotonic() + 5
            while harness.tasks._tasks[root_task].state != "terminal":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            record = harness.tasks._tasks[root_task]
            self.assertEqual(record.terminal_status, "ok")
            self.assertEqual(len(harness.agents), 2)
            child_records = [
                item for task_id, item in harness.tasks._tasks.items()
                if task_id != root_task
            ]
            self.assertEqual(len(child_records), 1)
            child = child_records[0]
            self.assertEqual(child.state, "terminal")
            evidence = harness.tasks.accept_result(
                child.descriptor.task_id, root.agent_id
            )
            self.assertEqual(evidence.content, b"child evidence")
            self.assertTrue(evidence.shared)
            self.assertTrue(
                any(row.get("kind") == "artifact_accepted" for row in root.private_context)
            )
        finally:
            harness.close()

    def test_child_authority_must_be_parent_subset(self):
        harness = nexus.NexusHarness(self.root, "x", self._policy())
        try:
            root = harness.spawn(
                nexus.AgentConfig(
                    self._policy().capabilities,
                    frozenset(),
                    "root",
                    resource_budget=16,
                    artifact_read_limit=256 * 1024,
                ),
                lambda _projection: {"type": "final", "content": "unused"},
            )
            task_id = harness.submit_root(root)
            task = harness.tasks.claim(task_id, root.agent_id)
            with self.assertRaisesRegex(nexus.HarnessError, "task_authority_subset"):
                harness.execute_action(
                    root,
                    task,
                    {
                        "type": "delegate",
                        "objective": "escalate",
                        "capabilities": ["WRITE_WORKSPACE"],
                        "tools": [],
                        "system_prompt": "invalid",
                        "label": "invalid",
                    },
                )
        finally:
            harness.close()

    def test_two_dynamic_children_settle_before_parent_final(self):
        harness = nexus.NexusHarness(
            self.root,
            "Split a generic objective when parallel work is useful",
            self._policy(),
            model_factory=lambda _config: _ChildModel(),
        )
        try:
            root = harness.spawn(
                nexus.AgentConfig(
                    self._policy().capabilities,
                    frozenset(),
                    "Plan parallel work.",
                    resource_budget=16,
                    artifact_read_limit=256 * 1024,
                ),
                _ParallelRootModel(),
            )
            root_task = harness.submit_root(root)
            deadline = time.monotonic() + 5
            while harness.tasks._tasks[root_task].state != "terminal":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            children = [
                record
                for task_id, record in harness.tasks._tasks.items()
                if task_id != root_task
            ]
            self.assertEqual(len(children), 2)
            self.assertTrue(
                all(
                    record.state == "terminal"
                    and record.terminal_status == "ok"
                    and record.result_artifact != 0
                    for record in children
                )
            )
            self.assertEqual(len(harness.agents), 3)
            self.assertEqual(
                sum(
                    row.get("event") == "TASK_DELEGATED"
                    for row in root.private_context
                ),
                2,
            )
            self.assertEqual(
                sum(
                    row.get("event") == "ARTIFACT_SHARED"
                    for row in root.private_context
                ),
                2,
            )
            self.assertEqual(
                sum(
                    row.get("event") == "TASK_COMPLETED"
                    for row in root.private_context
                ),
                3,
            )
            summary = harness.shared_context[-1]
            self.assertEqual(summary["kind"], "workflow_summary")
            self.assertEqual(summary["event"], "SUMMARY_COMMITTED")
            self.assertEqual(len(summary["task_graph"]), 3)
            self.assertTrue(summary["public_artifacts"])
            self.assertIn("sha256", summary)
        finally:
            harness.close()

    def test_artifact_store_enforces_share_and_pagination(self):
        policy = self._policy()
        config = nexus.AgentConfig(policy.capabilities, frozenset(), "artifact")
        store = nexus.ContextArtifactStore((7, 3), 8)
        artifact = store.put(config, 1, 9, 12, "file", "abcdef", shareable=True)
        with self.assertRaisesRegex(nexus.HarnessError, "artifact_read_denied"):
            store.read(config, 2, artifact.handle, 0, 3)
        store.share(config, 1, artifact.handle)
        self.assertEqual(store.read(config, 2, artifact.handle, 2, 3), b"cde")
        self.assertEqual(artifact.source_sequence, 12)
        self.assertEqual(artifact.lifecycle, (7, 3))

    def test_artifact_store_enforces_workflow_byte_and_read_accounts(self):
        policy = self._policy()
        config = nexus.AgentConfig(policy.capabilities, frozenset(), "artifact")
        byte_store = nexus.ContextArtifactStore(
            (8, 1), maximum=8, maximum_bytes=8, maximum_reads=64
        )
        byte_store.put(config, 1, 1, 1, "file", "abcdef")
        with self.assertRaisesRegex(nexus.HarnessError, "artifact_quota_exceeded"):
            byte_store.put(config, 2, 2, 1, "file", "xyz")

        read_store = nexus.ContextArtifactStore(
            (8, 2), maximum=8, maximum_bytes=64, maximum_reads=4
        )
        artifact = read_store.put(
            config, 1, 1, 1, "file", "abcdef", shareable=True
        )
        read_store.share(config, 1, artifact.handle)
        self.assertEqual(read_store.read(config, 1, artifact.handle, 0, 3), b"abc")
        with self.assertRaisesRegex(nexus.HarnessError, "artifact_read_quota"):
            read_store.read(config, 2, artifact.handle, 3, 2)

    def test_deepseek_round_repairs_multiple_actions_without_executing_them(self):
        policy = nexus.default_policy()
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "READ_WORKSPACE")),
            frozenset(("search_files",)),
            "Choose the next action.",
        )
        provider = _RepairProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        action = model({"task": {"objective": "inspect"}, "context": []})
        self.assertEqual(action["type"], "tool")
        self.assertEqual(action["tool"], "search_files")
        self.assertEqual(len(provider.requests), 3)
        first, selector, forced = (item[0] for item in provider.requests)
        self.assertNotEqual(first["corr_id"], selector["corr_id"])
        self.assertNotEqual(selector["corr_id"], forced["corr_id"])
        self.assertEqual(selector["tools"], [])
        self.assertEqual(forced["tool_choice"], {"tool": "search_files"})
        self.assertIn("exactly one next action", forced["system"])

    def test_deepseek_round_recovers_oversized_final_as_one_tool_action(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "READ_WORKSPACE")),
            frozenset(("search_files",)),
            "Choose the next action.",
        )
        provider = _OversizedFinalProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        action = model({"task": {"objective": "inspect"}, "context": []})
        self.assertEqual(action["type"], "tool")
        self.assertEqual(action["tool"], "search_files")
        self.assertEqual(len(provider.requests), 3)
        self.assertIn("admissible bounded action", provider.requests[1][0]["system"])


if __name__ == "__main__":
    unittest.main()
