#!/usr/bin/env python3
"""Focused tests for the task-independent Nexus multi-Agent Harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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
                "tool_use",
                tool="select_next_action",
                arguments={"action": "search_files"},
            )
        return nexus.relay.ModelReply(
            "tool_use",
            tool="search_files",
            arguments={"query": "main", "path": "user/src"},
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
            return nexus.relay.ModelReply(
                "tool_use",
                tool="select_next_action",
                arguments={"action": "search_files"},
            )
        return nexus.relay.ModelReply(
            "tool_use",
            tool="search_files",
            arguments={"query": "main", "path": "user/src"},
        )


class _IgnoredExactChoiceProvider:
    def __init__(self):
        self.requests = []

    def complete(self, request, *, deadline_monotonic=None):
        self.requests.append((dict(request), deadline_monotonic))
        choice = request.get("tool_choice")
        if len(self.requests) == 1:
            raise nexus.relay.ProviderError(
                "INCOMPLETE_MODEL_RESPONSE", "repair", retryable=True
            )
        if choice == {"tool": "select_next_action"}:
            return nexus.relay.ModelReply(
                "tool_use",
                tool="select_next_action",
                arguments={"action": "run_ucore_program"},
            )
        if choice == {"tool": "run_ucore_program"}:
            raise nexus.relay.ProviderError(
                "TOOL_CHOICE_MISMATCH", "ignored exact choice", retryable=True
            )
        return nexus.relay.ModelReply(
            "final",
            content=json.dumps(
                {
                    "build_id": "a" * 64,
                    "cases": [
                        {
                            "name": "normal",
                            "case_kind": "normal",
                            "stdin": "1+1\n",
                            "expected_output": "2",
                            "expected_exit": 0,
                        }
                    ],
                }
            ),
        )


class _RetryProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, request, *, deadline_monotonic=None):
        self.calls += 1
        if self.calls == 1:
            raise nexus.relay.ProviderError(
                "UPSTREAM_TIMEOUT", "temporary provider timeout", retryable=True
            )
        return nexus.relay.ModelReply("final", content="done")


class _CaptureFinalProvider:
    def __init__(self):
        self.requests = []

    def complete(self, request, *, deadline_monotonic=None):
        self.requests.append(dict(request))
        return nexus.relay.ModelReply("final", content="done")


class MultiAgentHarnessTests(unittest.TestCase):
    def test_catalog_manifest_prefix_accepts_relative_workspace_root(self):
        self.assertEqual(nexus._catalog_manifest_prefix(""), "")
        self.assertEqual(nexus._catalog_manifest_prefix("."), "")
        self.assertEqual(nexus._catalog_manifest_prefix("./user/src"), "user/src")
        self.assertEqual(nexus._catalog_manifest_prefix("user/include"), "user/include")

    def test_workspace_input_rejection_is_a_bounded_tool_result(self):
        result = nexus._workspace_tool_error(
            "search_files",
            "manifest",
            "error",
            "workspace_error=invalid_manifest_prefix",
            path="/",
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.workspace_generation, "")
        self.assertIn("status=rejected", result.content)
        self.assertIn("code=invalid_manifest_prefix", result.content)
        self.assertIn("tool=search_files", result.content)
        self.assertIn("path=/", result.content)
        self.assertEqual(
            nexus._result_projection(result.content),
            {"status": "rejected", "code": "invalid_manifest_prefix", "path": "/"},
        )

    def test_unfiltered_root_search_is_rejected_before_catalog_io(self):
        harness = nexus.NexusHarness(
            self.root, "generic development", nexus.default_policy()
        )
        provider = nexus.NativeProvider(
            2,
            nexus.AgentConfig(
                frozenset(("READ_WORKSPACE", "SHARE_ARTIFACT")),
                frozenset(("search_files", "read_file")),
                "controlled provider",
            ),
            "workspace-read",
        )
        harness.native_channel = mock.Mock()
        try:
            result = harness._workspace_catalog_tool(
                "search_files",
                {"path": ".", "query": "."},
                provider,
                2,
            )
            self.assertEqual(result.status, "error")
            self.assertIn("code=query_too_broad", result.content)
            harness.native_channel.catalog_load.assert_not_called()
        finally:
            harness.native_channel = None
            harness.close()

    def test_model_projection_stays_valid_and_keeps_newest_artifact(self):
        projection = {
            "goal": "develop one program",
            "agent": {"system_prompt": "p" * 2048},
            "task": {"objective": "o" * 4096, "input_artifact": "i" * 4096},
            "context": [{"sequence": index, "detail": "c" * 512} for index in range(20)],
            "shared_context": [
                {"sequence": index, "detail": "s" * 512} for index in range(20)
            ],
            "artifacts": [
                {
                    "handle": index,
                    "kind": "build_diagnostic",
                    "content": f"build_id={index}\n" + "x" * 2048,
                }
                for index in range(9, 1, -1)
            ],
            "contract_tool_remaining": {"run_ucore_program": 3},
        }
        encoded = nexus._model_projection_json(projection)
        self.assertLessEqual(len(encoded.encode("utf-8")), nexus.MAX_MODEL_PROJECTION_BYTES)
        parsed = json.loads(encoded)
        self.assertEqual(parsed["artifacts"][0]["handle"], 9)
        self.assertIn("build_id=9", parsed["artifacts"][0].get("content", ""))
        self.assertEqual(parsed["context"][-1]["sequence"], 19)

    def test_deepseek_hides_tools_with_no_contract_nodes_remaining(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "READ_WORKSPACE", "ORCHESTRATE")),
            frozenset(("search_files", "read_file")),
            "Choose one next action.",
        )
        provider = _CaptureFinalProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        action = model(
            {
                "task": {"objective": "inspect"},
                "context": [],
                "contract_tool_remaining": {
                    "search_files": 0,
                    "read_file": 1,
                    "delegate_task": 0,
                },
            }
        )
        self.assertEqual(action["type"], "final")
        names = {item["name"] for item in provider.requests[0]["tools"]}
        self.assertNotIn("search_files", names)
        self.assertNotIn("delegate_task", names)
        self.assertIn("read_file", names)
        self.assertIn("complete_task", names)

    def test_deepseek_hides_run_until_a_successful_build_is_settled(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "RUN")),
            frozenset(("run_ucore_program",)),
            "Choose one next action.",
        )
        provider = _CaptureFinalProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        model(
            {
                "task": {"objective": "build and run"},
                "context": [],
                "contract_tool_remaining": {"run_ucore_program": 3},
            }
        )
        names = {item["name"] for item in provider.requests[-1]["tools"]}
        self.assertNotIn("run_ucore_program", names)

        model(
            {
                "task": {"objective": "build and run"},
                "context": [
                    {
                        "kind": "tool_result",
                        "tool": "write_file",
                        "evidence": {"status": "passed", "revision": "a" * 64},
                    },
                    {
                        "kind": "tool_result",
                        "tool": "build_ucore_program",
                        "evidence": {
                            "status": "passed",
                            "source_revision": "a" * 64,
                            "build_id": "b" * 64,
                        },
                    }
                ],
                "contract_tool_remaining": {"run_ucore_program": 3},
            }
        )
        names = {item["name"] for item in provider.requests[-1]["tools"]}
        self.assertIn("run_ucore_program", names)

    def test_deepseek_requires_mutation_and_does_not_repeat_settled_build(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "READ_WORKSPACE", "WRITE_WORKSPACE", "BUILD", "RUN")),
            frozenset(
                (
                    "search_files", "read_file", "write_file", "apply_patch",
                    "build_ucore_program", "run_ucore_program",
                )
            ),
            "Develop one program.",
        )
        provider = _CaptureFinalProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        model(
            {
                "task": {"objective": "develop"},
                "context": [],
                "contract_tool_remaining": dict(nexus.CONTRACT_TOOL_BUDGET),
            }
        )
        initial = {item["name"] for item in provider.requests[-1]["tools"]}
        self.assertNotIn("build_ucore_program", initial)
        self.assertIn("write_file", initial)

        revision = "a" * 64
        model(
            {
                "task": {"objective": "develop"},
                "context": [
                    {
                        "kind": "tool_result",
                        "tool": "write_file",
                        "evidence": {"status": "passed", "revision": revision},
                    },
                    {
                        "kind": "tool_result",
                        "tool": "build_ucore_program",
                        "evidence": {
                            "status": "passed",
                            "source_revision": revision,
                            "build_id": "b" * 64,
                        },
                    },
                ],
                "contract_tool_remaining": dict(nexus.CONTRACT_TOOL_BUDGET),
            }
        )
        settled = {item["name"] for item in provider.requests[-1]["tools"]}
        self.assertNotIn("build_ucore_program", settled)
        self.assertNotIn("search_files", settled)
        self.assertIn("run_ucore_program", settled)

    def test_kernel_status_monitor_publishes_one_bounded_snapshot(self):
        status = {name: 0 for name in nexus.native_task.STATUS_FIELDS}
        status.update({
            "version": nexus.native_task.STATUS_VERSION,
            "tick": 42,
            "tasks_active": 1,
            "sq_depth": 0,
            "cq_depth": 0,
            "lifecycle_id": 3,
            "lifecycle_generation": 1,
        })

        class Channel:
            def status(self):
                return dict(status)

        rows = []
        monitor = None

        class Events:
            @staticmethod
            def emit(source, kind, message, **fields):
                rows.append((source, kind, message, fields))
                monitor._stop.set()

        monitor = nexus.KernelStatusMonitor(Channel(), Events(), 1.0)
        monitor._run()
        self.assertEqual(rows[0][0:2], ("kernel", "kernel_status"))
        self.assertEqual(rows[0][3]["tick"], 42)
        self.assertIn("SQ/CQ 0/0", rows[0][2])

    def test_isolated_cli_bootstraps_sibling_modules(self):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(_HERE / "agentos_nexus_multiagent.py"),
                "--help",
            ],
            cwd=_HERE.parent,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workspace", result.stdout)
        self.assertIn("--native-boot-timeout", result.stdout)
        self.assertIn("--progress", result.stdout)
        self.assertIn("--status-interval", result.stdout)

    def test_trace_contains_model_task_and_artifact_progress(self):
        trace = self.root / "progress.ndjson"
        harness = nexus.NexusHarness(
            self.root, "Finish one generic task", self._policy(), trace_path=trace
        )
        try:
            config = nexus.AgentConfig(
                self._policy().capabilities,
                frozenset(),
                "Finish directly.",
                resource_budget=4,
                artifact_read_limit=8192,
            )
            agent = harness.spawn(
                config,
                lambda _projection: {"type": "final", "content": "done"},
                "trace-agent",
            )
            task_id = harness.submit_root(agent)
            deadline = time.monotonic() + 5
            while harness.tasks._tasks[task_id].state != "terminal":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
        finally:
            harness.close()
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        kinds = {row["kind"] for row in rows}
        self.assertTrue(
            {
                "agent_spawned",
                "root_task_submitted",
                "model_started",
                "model_completed",
                "artifact_sealed",
                "task_completed",
            }
            <= kinds
        )
        self.assertEqual(
            [row["progress_sequence"] for row in rows],
            list(range(1, len(rows) + 1)),
        )

    def test_model_failure_is_visible_before_task_failure(self):
        trace = self.root / "failed-progress.ndjson"
        harness = nexus.NexusHarness(
            self.root, "Fail one generic task", self._policy(), trace_path=trace
        )
        try:
            config = nexus.AgentConfig(
                self._policy().capabilities,
                frozenset(),
                "Fail predictably.",
                resource_budget=4,
                artifact_read_limit=8192,
            )

            def fail_model(_projection):
                raise RuntimeError("provider unavailable")

            agent = harness.spawn(config, fail_model, "failed-model-agent")
            task_id = harness.submit_root(agent)
            deadline = time.monotonic() + 5
            while harness.tasks._tasks[task_id].state != "terminal":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
        finally:
            harness.close()
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        kinds = [row["kind"] for row in rows]
        self.assertLess(kinds.index("model_started"), kinds.index("model_failed"))
        self.assertLess(kinds.index("model_failed"), kinds.index("task_completed"))
        failed = next(row for row in rows if row["kind"] == "model_failed")
        self.assertEqual(failed["error"], "RuntimeError")
        self.assertNotIn("Traceback", failed["detail"])

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

    def test_provider_groups_preserve_three_dynamic_agent_slots(self):
        harness = nexus.NexusHarness(
            self.root, "generic development", nexus.default_policy()
        )
        try:
            build = harness._provider_group("build_ucore_program")
            run = harness._provider_group("run_ucore_program")
            self.assertEqual(build[0], "execution")
            self.assertEqual(run[0], "execution")
            self.assertEqual(build, run)
            self.assertEqual(nexus.MAX_AGENTS, 7)
            self.assertEqual(
                set(("workspace-read", "workspace-mutation", "execution")),
                {
                    harness._provider_group("search_files")[0],
                    harness._provider_group("write_file")[0],
                    build[0],
                },
            )
            self.assertEqual(nexus.MAX_AGENTS - 1 - 3, 3)
        finally:
            harness.close()

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

    def test_development_completion_requires_native_read_mutation_build_and_cases(self):
        policy = nexus.default_policy()
        harness = nexus.NexusHarness(self.root, "generic repair", policy)
        config = nexus.AgentConfig(
            policy.capabilities,
            policy.tools,
            "verify settled development evidence",
            resource_budget=16,
            artifact_count_limit=64,
            artifact_bytes_limit=2 * 1024 * 1024,
            artifact_read_limit=2 * 1024 * 1024,
        )
        root_descriptor = nexus.TaskDescriptor(
            1, 1, 0, 1, 1, 1, 0, 2,
            config.capabilities, config.tools, "", 16, 1024,
            time.monotonic() + 60, "final", "delegate_task",
        )
        harness.tasks._tasks[1] = nexus.TaskRecord(
            root_descriptor, state="claimed", claimed_by=1
        )

        def add_result(task_id, tool, kind, content):
            artifact = harness.store.put(
                config, 2, task_id, task_id, kind, content,
                shareable=True,
            )
            harness.store.share(config, 2, artifact.handle)
            descriptor = nexus.TaskDescriptor(
                task_id, task_id, 1, 1, 2, task_id * 2,
                task_id * 2, artifact.handle,
                frozenset((nexus.TOOL_CAPABILITY[tool],)),
                frozenset((tool,)), "", 1, 1024,
                time.monotonic() + 60, kind, tool,
            )
            harness.tasks._tasks[task_id] = nexus.TaskRecord(
                descriptor, state="terminal", result_artifact=artifact.handle,
                terminal_status="ok", claimed_by=2,
            )

        try:
            self.assertIn("workspace_read", harness._development_completion_missing(1))
            self.assertIn("workspace_mutation", harness._development_completion_missing(1))
            add_result(
                2, "search_files", "search",
                "catalog_evidence_v1\nworkspace_generation=" + "a" * 64
                + "\nused_index=1\ncatalog_records=8\ncatalog_candidates=1",
            )
            add_result(
                3, "write_file", "file",
                "workspace_write\nrevision=" + "b" * 64 + "\natomic_commit=1",
            )
            build_id = "c" * 64
            add_result(
                4, "build_ucore_program", "build_diagnostic",
                "ucore_build\nstatus=passed\nsource_revision="
                + "b" * 64
                + f"\nbuild_id={build_id}",
            )
            add_result(
                5, "run_ucore_program", "test_result",
                "ucore_run_suite\nstatus=passed\n"
                f"build_id={build_id}\nsource_revision="
                + "b" * 64
                + "\ncase_kinds=normal,invalid,failure",
            )
            self.assertEqual(harness._development_completion_missing(1), [])
            add_result(
                6, "write_file", "file",
                "workspace_write\nrevision=" + "d" * 64 + "\natomic_commit=1",
            )
            self.assertIn(
                "successful_build", harness._development_completion_missing(1)
            )
        finally:
            harness.close()

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
        self.assertEqual(selector["tool_choice"], {"tool": "select_next_action"})
        self.assertEqual(selector["tools"][0]["name"], "select_next_action")
        self.assertIn(
            "search_files",
            selector["tools"][0]["input_schema"]["properties"]["action"]["enum"],
        )
        self.assertEqual(forced["tool_choice"], {"tool": "search_files"})
        self.assertEqual(forced["tools"][0]["name"], "search_files")
        self.assertEqual(forced["tools"][0]["input_schema"], nexus.TOOL_SCHEMAS["search_files"])
        self.assertIn("Guest Metadata Catalog", forced["tools"][0]["description"])
        self.assertIn("workspace-relative", forced["tools"][0]["description"])
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

    def test_deepseek_round_recovers_ignored_exact_choice_with_strict_json(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT", "RUN")),
            frozenset(("run_ucore_program",)),
            "Choose the next action.",
        )
        provider = _IgnoredExactChoiceProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        action = model(
            {
                "task": {"objective": "test"},
                "context": [
                    {
                        "kind": "tool_result",
                        "tool": "write_file",
                        "evidence": {"status": "passed", "revision": "a" * 64},
                    },
                    {
                        "kind": "tool_result",
                        "tool": "build_ucore_program",
                        "evidence": {
                            "status": "passed",
                            "source_revision": "a" * 64,
                            "build_id": "a" * 64,
                        },
                    }
                ],
                "contract_tool_remaining": {"run_ucore_program": 1},
            }
        )
        self.assertEqual(action["type"], "tool")
        self.assertEqual(action["tool"], "run_ucore_program")
        self.assertEqual(action["arguments"]["cases"][0]["case_kind"], "normal")
        argument_request = provider.requests[-1][0]
        self.assertEqual(argument_request["tool_choice"], "none")
        self.assertEqual(argument_request["tools"], [])

    def test_deepseek_provider_retry_emits_bounded_attempt_events(self):
        config = nexus.AgentConfig(
            frozenset(("READ_CONTEXT",)), frozenset(), "Finish."
        )
        provider = _RetryProvider()
        model = nexus.DeepSeekModel(provider, config, "deepseek-chat")
        rows = []
        model.bind_progress(
            lambda kind, message, fields: rows.append(
                {"kind": kind, "message": message, **dict(fields)}
            )
        )
        model.set_progress_context(task_id=7, model_round=2)
        with mock.patch.object(nexus.time, "sleep") as sleep:
            reply = model._complete_with_retry({"corr_id": 99}, attempts=2)
        self.assertEqual(reply.content, "done")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(sleep.call_args.args[0], 0.5)
        self.assertEqual(
            [row["kind"] for row in rows],
            [
                "model_request_started",
                "model_request_failed",
                "model_request_retrying",
                "model_request_started",
                "model_request_completed",
            ],
        )
        self.assertEqual(rows[1]["error_code"], "UPSTREAM_TIMEOUT")
        self.assertEqual(rows[-1]["task_id"], 7)


if __name__ == "__main__":
    unittest.main()
