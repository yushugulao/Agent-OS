#!/usr/bin/env python3
"""Checks for the persistent native Task Channel bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_native_task_channel as native
import agentos_nexus_multiagent as nexus


class _HarnessRootModel:
    def __init__(self) -> None:
        self.delegated = False

    def __call__(self, projection):
        completed = any(
            row.get("kind") == "task_completed" and row.get("status") == "ok"
            for row in projection["context"]
        )
        if not self.delegated:
            self.delegated = True
            return {
                "type": "delegate",
                "objective": "Return one sealed native Task Channel report.",
                "capabilities": ["READ_CONTEXT", "SHARE_ARTIFACT"],
                "tools": [],
                "system_prompt": "Return the requested report.",
                "label": "configured-child",
                "resource_budget": 4,
                "read_budget": 8192,
                "expected_result_kind": "subtask_report",
            }
        if not completed:
            return {"type": "wait"}
        return {"type": "final", "content": "native child report accepted"}


def _harness_child_model(_projection):
    return {"type": "final", "content": "sealed child evidence"}


class _HarnessReadModel:
    def __call__(self, projection):
        completed_reads = sum(
            row.get("kind") == "tool_result" and row.get("tool") == "read_file"
            for row in projection["context"]
        )
        if completed_reads < 2:
            return {
                "type": "tool",
                "tool": "read_file",
                "arguments": {"path": "user/src/sample.c", "start_line": 1,
                              "max_lines": 8},
            }
        return {"type": "final", "content": "native read Artifact accepted"}


class NativeMappingTests(unittest.TestCase):
    def test_capabilities_and_tools_map_to_frozen_kernel_bits(self):
        self.assertEqual(
            native._capability_mask(frozenset(("READ_CONTEXT", "ORCHESTRATE"))),
            (1 << 0) | (1 << 1) | (1 << 9) | (1 << 11) | (1 << 12),
        )
        self.assertEqual(
            native._capability_mask(frozenset(("READ_WORKSPACE",))),
            (1 << 0) | (1 << 1) | (1 << 4) | (1 << 8),
        )
        self.assertEqual(
            native._tool_mask(frozenset(("write_file", "run_ucore_program"))),
            (1 << 27) | (1 << 32),
        )

    def test_workspace_revision_is_always_one_sha256(self):
        self.assertEqual(native._revision_digest("a" * 64), "a" * 64)
        self.assertEqual(len(native._revision_digest("workspace-v3")), 64)


def integration(qemu: str, kernel: Path, image: Path) -> int:
    capabilities = frozenset(native.CAPABILITY_BITS)
    tools = frozenset(native.TOOL_IDS)
    config = SimpleNamespace(
        capabilities=capabilities,
        tools=tools,
        resource_budget=32,
        artifact_count_limit=32,
        artifact_bytes_limit=256 * 1024,
        artifact_read_limit=1024 * 1024,
        summary_high_watermark=24,
    )
    descriptor = SimpleNamespace(
        task_id=1,
        correlation_id=1001,
        parent_task_id=0,
        parent_agent=1,
        target_agent=1,
        objective_artifact=1,
        input_artifact=0,
        result_artifact=7,
        required_capabilities=capabilities,
        allowed_tools=tools,
        workspace_revision="integration-workspace",
        resource_budget=16,
        read_budget=64 * 1024,
        deadline_monotonic=time.monotonic() + 60.0,
        expected_result_kind="final",
        operation_tool="delegate_task",
    )
    with native.NativeTaskChannel(qemu=qemu, kernel=kernel, image=image) as channel:
        try:
            identity = channel.spawn(1, config)
        except native.NativeTaskChannelError:
            print("\n".join(channel.diagnostic_tail()), file=sys.stderr)
            raise
        child_identity = channel.spawn(2, config)
        if min(identity.values()) <= 0:
            raise native.NativeTaskChannelError("integration_identity_invalid")
        initial_status = channel.status()
        if (
            initial_status["version"] != native.STATUS_VERSION
            or initial_status["agents_active"] != 2
            or initial_status["tasks_active"] != 0
            or initial_status["tasks_pending"] != 0
            or initial_status["tasks_claimed"] != 0
            or initial_status["tasks_terminal"] != 0
            or initial_status["lifecycle_id"] != channel.lifecycle[0]
            or initial_status["lifecycle_generation"] != channel.lifecycle[1]
        ):
            raise native.NativeTaskChannelError("integration_status_initial")

        # The public Harness capacity is eight identities.  Exercise the actual
        # control Guest at that capacity before task effects begin, so controller
        # pipe capacity and inherited-worker cleanup have behavioral coverage.
        for host_agent_id in range(3, nexus.MAX_AGENTS + 1):
            try:
                channel.spawn(host_agent_id, config, channel_owner=False)
            except native.NativeTaskChannelError as error:
                raise native.NativeTaskChannelError(
                    f"integration_agent_capacity_at_{host_agent_id}:{error}:"
                    + "|".join(channel.diagnostic_tail()[-8:])
                ) from error
        capacity_status = channel.status()
        if capacity_status["agents_active"] != nexus.MAX_AGENTS:
            raise native.NativeTaskChannelError(
                f"integration_agent_capacity:{capacity_status}"
            )
        channel.delegate(descriptor)
        channel.claim(1, 1)
        child_descriptor = SimpleNamespace(**vars(descriptor))
        child_descriptor.task_id = 2
        child_descriptor.correlation_id = 1002
        child_descriptor.parent_task_id = 1
        child_descriptor.parent_agent = 1
        child_descriptor.target_agent = 2
        child_descriptor.objective_artifact = 2
        child_descriptor.result_artifact = 8
        child_descriptor.expected_result_kind = "subtask_report"
        channel.delegate(child_descriptor)
        channel.claim(2, 2)
        claimed_status = channel.status()
        if (
            claimed_status["tasks_active"] != 2
            or claimed_status["tasks_pending"] != 0
            or claimed_status["tasks_claimed"] != 2
            or claimed_status["tasks_terminal"] != 0
        ):
            raise native.NativeTaskChannelError("integration_status_claimed")
        try:
            child_artifact = channel.seal_artifact(
                host_agent_id=2,
                task_id=2,
                handle=8,
                kind="subtask_report",
                tool="delegate_task",
                content=b"native child evidence",
                host_context_sequence=1,
            )
        except native.NativeTaskChannelError:
            print("\n".join(channel.diagnostic_tail()), file=sys.stderr)
            raise
        try:
            child_completion = channel.complete(2, 2, "ok", 8)
        except native.NativeTaskChannelError:
            print("\n".join(channel.diagnostic_tail()), file=sys.stderr)
            raise
        channel.bind_artifact(
            host_agent_id=1,
            task_id=2,
            handle=8,
            kind="subtask_report",
            tool="delegate_task",
            length=21,
            sha256=str(child_artifact["sha256"]),
            host_context_sequence=2,
            cause_sequence=child_completion["context_sequence"],
        )
        cancelled_descriptor = SimpleNamespace(**vars(child_descriptor))
        cancelled_descriptor.task_id = 3
        cancelled_descriptor.correlation_id = 1003
        cancelled_descriptor.objective_artifact = 3
        cancelled_descriptor.result_artifact = 9
        cancelled_descriptor.deadline_monotonic = time.monotonic() + 60.0
        channel.delegate(cancelled_descriptor)
        channel.claim(3, 2)
        first_cancel = channel.request_cancel(3)
        second_cancel = channel.request_cancel(3)
        if first_cancel != second_cancel or first_cancel["state"] != 2:
            raise native.NativeTaskChannelError("integration_cancel_idempotency")
        cancelled_completion = channel.collect_cancel(3)
        if cancelled_completion["status"] != native.STATUS_IDS["cancelled"]:
            raise native.NativeTaskChannelError("integration_cancel_terminal")
        channel.seal_artifact(
            host_agent_id=1,
            task_id=1,
            handle=7,
            kind="final",
            tool="delegate_task",
            content=b"native root evidence",
            host_context_sequence=3,
        )
        channel.complete(1, 1, "ok", 7)
        if channel._claimed:
            raise native.NativeTaskChannelError("integration_task_not_drained")
        final_status = channel.status()
        if (
            final_status["tasks_active"] != 0
            or final_status["tasks_pending"] != 0
            or final_status["tasks_claimed"] != 0
            or final_status["tasks_terminal"] < 3
            or final_status["submitted"] < 3
            or final_status["completed"] < 3
            or final_status["context_count"] < 1
            or final_status["task_wait_count"] < 3
            or final_status["last_heartbeat_tick"] <= 0
        ):
            raise native.NativeTaskChannelError(
                f"integration_status_final:{final_status}"
            )
        fence = channel.fence(9001)
        print(
            "agentos-native-task-channel: PASS "
            f"lifecycle={channel.lifecycle[0]}/{channel.lifecycle[1]} "
            f"agents={capacity_status['agents_active']} "
            f"tasks=3 nested=1 cancelled=1 terminal=ok status_tick={final_status['tick']} "
            f"fence={fence['fence_sequence']}"
        )
    with tempfile.TemporaryDirectory(prefix="agentos-native-harness-") as root:
        channel = native.NativeTaskChannel(qemu=qemu, kernel=kernel, image=image)
        policy = nexus.WorkflowPolicy(
            frozenset(("READ_CONTEXT", "ORCHESTRATE", "SHARE_ARTIFACT")),
            frozenset(),
            resource_budget=16,
            artifact_count_limit=32,
            artifact_bytes_limit=256 * 1024,
            artifact_read_limit=256 * 1024,
        )
        harness = nexus.NexusHarness(
            Path(root),
            "Exercise one generic delegated workflow.",
            policy,
            model_factory=lambda _config: _harness_child_model,
            native_channel=channel,
        )
        try:
            root_config = nexus.AgentConfig(
                policy.capabilities,
                policy.tools,
                "Plan and accept one generic child result.",
                resource_budget=16,
                artifact_read_limit=256 * 1024,
            )
            root_agent = harness.spawn(
                root_config, _HarnessRootModel(), "configured-root"
            )
            root_task = harness.submit_root(root_agent, deadline_seconds=60.0)
            deadline = time.monotonic() + 30.0
            while harness.tasks._tasks[root_task].state != "terminal":
                if time.monotonic() >= deadline:
                    raise native.NativeTaskChannelError(
                        "native_harness_workflow_timeout"
                    )
                time.sleep(0.01)
            if harness.tasks._tasks[root_task].terminal_status != "ok":
                raise native.NativeTaskChannelError(
                    "native_harness_workflow_failed"
                )
            if len(harness.agents) != 2 or channel._claimed:
                raise native.NativeTaskChannelError(
                    "native_harness_task_not_drained"
                )
            if any(
                min(agent.native_pid, agent.native_agent_id, agent.native_control_id)
                <= 0
                for agent in harness.agents.values()
            ):
                raise native.NativeTaskChannelError(
                    "native_harness_identity_invalid"
                )
            print(
                "agentos-native-harness: PASS "
                f"lifecycle={harness.lifecycle[0]}/{harness.lifecycle[1]} "
                "agents=2 tasks=2 generic_loop=1"
            )
        finally:
            harness.close()
    with tempfile.TemporaryDirectory(prefix="agentos-native-tool-") as root:
        workspace_root = Path(root)
        (workspace_root / "user" / "src").mkdir(parents=True)
        (workspace_root / "user" / "src" / "sample.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        channel = native.NativeTaskChannel(qemu=qemu, kernel=kernel, image=image)
        policy = nexus.WorkflowPolicy(
            frozenset(("READ_CONTEXT", "READ_WORKSPACE", "SHARE_ARTIFACT")),
            frozenset(("read_file",)), resource_budget=8,
            artifact_count_limit=16, artifact_bytes_limit=128 * 1024,
            artifact_read_limit=128 * 1024,
        )
        harness = nexus.NexusHarness(
            workspace_root, "Read one workspace source through a native Task.",
            policy, native_channel=channel,
        )
        try:
            config = nexus.AgentConfig(
                policy.capabilities, policy.tools, "Read and report evidence.",
                resource_budget=8, artifact_count_limit=16,
                artifact_bytes_limit=128 * 1024,
                artifact_read_limit=128 * 1024,
            )
            agent = harness.spawn(config, _HarnessReadModel(), "read-agent")
            root_task = harness.submit_root(agent, deadline_seconds=60.0)
            deadline = time.monotonic() + 45.0
            while harness.tasks._tasks[root_task].state != "terminal":
                if time.monotonic() >= deadline:
                    print(
                        "native tool timeout context: "
                        + repr(agent.private_context[-8:]),
                        file=sys.stderr,
                    )
                    print("\n".join(channel.diagnostic_tail()), file=sys.stderr)
                    raise native.NativeTaskChannelError("native_tool_workflow_timeout")
                time.sleep(0.01)
            native_tool_tasks = [
                row for row in harness.tasks._tasks.values()
                if row.descriptor.operation_tool == "read_file"
            ]
            if (
                len(native_tool_tasks) != 2
                or any(item.state != "terminal" for item in native_tool_tasks)
                or any(item.terminal_status != "ok" for item in native_tool_tasks)
                or any(item.result_artifact <= 0 for item in native_tool_tasks)
                or any(item.native_context_sequence <= 0 for item in native_tool_tasks)
            ):
                print(
                    "native tool task diagnostic: "
                    + repr(
                        [
                            (
                                item.descriptor.task_id,
                                item.descriptor.operation_tool,
                                item.state,
                                item.terminal_status,
                                item.native_context_sequence,
                            )
                            for item in harness.tasks._tasks.values()
                        ]
                    ),
                    file=sys.stderr,
                )
                print(
                    "native tool context diagnostic: "
                    + repr(agent.private_context[-8:]),
                    file=sys.stderr,
                )
                print("\n".join(channel.diagnostic_tail()), file=sys.stderr)
                raise native.NativeTaskChannelError("native_tool_task_missing")
            artifacts = [
                harness.store.get(item.result_artifact)
                for item in native_tool_tasks
            ]
            artifact = artifacts[-1]
            if (
                artifact.native_context_sequence <= 0
                or b"int main" not in artifact.content
                or b"used_index=1" not in artifact.content
                or b"catalog_records=2" not in artifact.content
                or b"catalog_candidates=1" not in artifact.content
                or b"host_body_reads=1" not in artifact.content
                or b"watch_events=0" in artifact.content
                or b"reused_pages=1" not in artifact.content
            ):
                raise native.NativeTaskChannelError("native_tool_artifact_missing")
            print(
                "agentos-native-brokered-tool: PASS "
                f"tasks={len(native_tool_tasks)} "
                f"artifact={artifact.handle} context={artifact.native_context_sequence} "
                "catalog_index=1 reuse=1"
            )
        finally:
            harness.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    if args.integration:
        if args.kernel is None or args.image is None:
            parser.error("--kernel and --image are required for --integration")
        return integration(args.qemu, args.kernel, args.image)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeMappingTests)
    result = unittest.TextTestRunner().run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
