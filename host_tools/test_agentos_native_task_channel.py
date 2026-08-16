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


class NativeMappingTests(unittest.TestCase):
    def test_capabilities_and_tools_map_to_frozen_kernel_bits(self):
        self.assertEqual(
            native._capability_mask(frozenset(("READ_CONTEXT", "ORCHESTRATE"))),
            (1 << 0) | (1 << 1) | (1 << 9) | (1 << 11) | (1 << 12),
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
        parent_task_id=0,
        parent_agent=1,
        target_agent=1,
        objective_artifact=1,
        input_artifact=0,
        required_capabilities=capabilities,
        allowed_tools=tools,
        workspace_revision="integration-workspace",
        resource_budget=16,
        read_budget=64 * 1024,
        deadline_monotonic=time.monotonic() + 60.0,
        expected_result_kind="final",
    )
    with native.NativeTaskChannel(qemu=qemu, kernel=kernel, image=image) as channel:
        identity = channel.spawn(1, config)
        child_identity = channel.spawn(2, config)
        if min(identity.values()) <= 0:
            raise native.NativeTaskChannelError("integration_identity_invalid")
        channel.delegate(descriptor)
        channel.claim(1, 1)
        child_descriptor = SimpleNamespace(**vars(descriptor))
        child_descriptor.task_id = 2
        child_descriptor.parent_task_id = 1
        child_descriptor.parent_agent = 1
        child_descriptor.target_agent = 2
        child_descriptor.objective_artifact = 2
        channel.delegate(child_descriptor)
        channel.claim(2, 2)
        channel.complete(2, 2, "ok", 8)
        channel.complete(1, 1, "ok", 7)
        if channel._claimed:
            raise native.NativeTaskChannelError("integration_task_not_drained")
        print(
            "agentos-native-task-channel: PASS "
            f"lifecycle={channel.lifecycle[0]}/{channel.lifecycle[1]} "
            f"agents={identity['agent_id']},{child_identity['agent_id']} "
            "tasks=2 nested=1 terminal=ok"
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
