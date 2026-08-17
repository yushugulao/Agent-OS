#!/usr/bin/env python3
"""Deterministic native Harness repair loop with real build and QEMU runs."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys
import time


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_native_task_channel as native
import agentos_nexus_dev as dev
import agentos_nexus_multiagent as nexus


RELATIVE = "user/src/nexus_harness_probe_ucore.c"
TARGET = "nexus_harness_probe_ucore"


def _sources() -> tuple[str, str, str]:
    broken = (
        "#include <stdio.h>\n"
        "#include <unistd.h>\n\n"
        "int main(void)\n"
        "{\n"
        "\tchar input[32];\n"
        "\tint count = read(0, input, sizeof(input));\n"
        "\tif (count <= 0) {\n"
        "\t\tprintf(\"error=empty\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\treturn nexus_missing_handler(input, count);\n"
        "}\n"
    )
    fixed = (
        "#include <stdio.h>\n"
        "#include <unistd.h>\n\n"
        "int main(void)\n"
        "{\n"
        "\tchar input[32];\n"
        "\tint count = read(0, input, sizeof(input));\n"
        "\tif (count <= 0) {\n"
        "\t\tprintf(\"error=empty\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\tif (count >= 2 && input[0] == 'o' && input[1] == 'k') {\n"
        "\t\tprintf(\"result=42\\n\");\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tif (count >= 3 && input[0] == 'b' && input[1] == 'a' && input[2] == 'd') {\n"
        "\t\tprintf(\"error=invalid\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\tprintf(\"error=failure\\n\");\n"
        "\treturn 3;\n"
        "}\n"
    )
    patch = (
        f"--- a/{RELATIVE}\n"
        f"+++ b/{RELATIVE}\n"
        f"@@ -1,{len(broken.splitlines())} +1,{len(fixed.splitlines())} @@\n"
        + "".join(f"-{line}\n" for line in broken.splitlines())
        + "".join(f"+{line}\n" for line in fixed.splitlines())
    )
    return broken, fixed, patch


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fields(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result.setdefault(key, value)
    return result


class RepairLoopModel:
    """A deterministic model substitute; the product runtime stays task-neutral."""

    def __init__(self) -> None:
        self.step = 0
        self.broken, self.fixed, self.patch = _sources()

    @staticmethod
    def _successful_build(projection: dict[str, object]) -> str:
        artifacts = projection.get("artifacts", [])
        if not isinstance(artifacts, list):
            return ""
        for artifact in reversed(artifacts):
            if not isinstance(artifact, dict) or artifact.get("kind") != "build_diagnostic":
                continue
            content = artifact.get("content")
            if not isinstance(content, str):
                continue
            fields = _fields(content)
            build_id = fields.get("build_id", "")
            if fields.get("status") == "passed" and re.fullmatch(r"[0-9a-f]{64}", build_id):
                return build_id
        return ""

    def __call__(self, projection: dict[str, object]) -> dict[str, object]:
        self.step += 1
        if self.step == 1:
            # A normal model mistake must settle as a native error Artifact and
            # leave the long-running Guest available for the corrected request.
            return {
                "type": "tool",
                "tool": "search_files",
                "arguments": {
                    "query": "provider_loop",
                    "path": "/",
                    "stage": "application",
                    "kind": "source",
                    "status": "current",
                    "summary_contains": "agentharness",
                },
            }
        if self.step == 2:
            return {
                "type": "tool",
                "tool": "search_files",
                "arguments": {
                    "query": "provider_loop",
                    "path": "user/src",
                    "stage": "application",
                    "kind": "source",
                    "status": "current",
                    "summary_contains": "agentharness",
                },
            }
        if self.step == 3:
            return {
                "type": "tool",
                "tool": "write_file",
                "arguments": {
                    "path": RELATIVE,
                    "content": self.broken,
                    "expected_revision": dev.MISSING_REVISION,
                    "write_id": "",
                    "commit": 1,
                },
            }
        if self.step == 4:
            return {
                "type": "tool",
                "tool": "build_ucore_program",
                "arguments": {
                    "source_path": RELATIVE,
                    "source_revision": _digest(self.broken),
                    "target": TARGET,
                },
            }
        if self.step == 5:
            return {
                "type": "tool",
                "tool": "apply_patch",
                "arguments": {
                    "path": RELATIVE,
                    "patch": self.patch,
                    "expected_revision": _digest(self.broken),
                },
            }
        if self.step == 6:
            return {
                "type": "tool",
                "tool": "build_ucore_program",
                "arguments": {
                    "source_path": RELATIVE,
                    "source_revision": _digest(self.fixed),
                    "target": TARGET,
                },
            }
        if self.step == 7:
            build_id = self._successful_build(projection)
            if not build_id:
                raise RuntimeError("successful_build_artifact_missing")
            return {
                "type": "tool",
                "tool": "run_ucore_program",
                "arguments": {
                    "build_id": build_id,
                    "cases": [
                        {
                            "name": "normal",
                            "stdin": "ok\n",
                            "expected_output": "result=42",
                            "expected_exit": 0,
                            "case_kind": "normal",
                        },
                        {
                            "name": "invalid",
                            "stdin": "bad\n",
                            "expected_output": "error=invalid",
                            "expected_exit": 2,
                            "case_kind": "invalid",
                        },
                        {
                            "name": "failure",
                            "stdin": "fail\n",
                            "expected_output": "error=failure",
                            "expected_exit": 3,
                            "case_kind": "failure",
                        },
                    ],
                },
            }
        return {
            "type": "final",
            "content": "Read, repaired, rebuilt, and verified three independent Guest cases.",
        }


def integration(workspace: Path, qemu: str, kernel: Path, image: Path) -> int:
    root = workspace.resolve(strict=True)
    source = root / RELATIVE
    if source.exists() or source.is_symlink():
        raise RuntimeError("native_integration_probe_path_already_exists")
    channel = native.NativeTaskChannel(qemu=qemu, kernel=kernel, image=image)
    policy = nexus.default_policy()
    events = nexus.harness_progress.HarnessEventBus(
        mode="plain", goal="deterministic native Harness repair integration"
    )
    harness = nexus.NexusHarness(
        root,
        "Repair a generic broken uCore program and verify normal, invalid, and failure cases.",
        policy,
        native_channel=channel,
        event_bus=events,
    )
    try:
        config = nexus.AgentConfig(
            policy.capabilities,
            policy.tools,
            "Use the available general tools and accept only real Guest evidence.",
            resource_budget=16,
            artifact_count_limit=64,
            artifact_bytes_limit=2 * 1024 * 1024,
            artifact_read_limit=2 * 1024 * 1024,
        )
        agent = harness.spawn(config, RepairLoopModel(), "repair-loop")
        root_task = harness.submit_root(agent, deadline_seconds=420.0)
        deadline = time.monotonic() + 360.0
        while harness.tasks._tasks[root_task].state != "terminal":
            if time.monotonic() >= deadline:
                raise RuntimeError("native_repair_loop_timeout")
            time.sleep(0.02)
        root_record = harness.tasks._tasks[root_task]
        if root_record.terminal_status != "ok":
            detail = [
                (
                    record.descriptor.task_id,
                    record.descriptor.operation_tool,
                    record.state,
                    record.terminal_status,
                    record.native_context_sequence,
                )
                for record in harness.tasks._tasks.values()
            ]
            raise RuntimeError(
                "native_repair_loop_failed:"
                f"tasks={detail!r}:context={agent.private_context[-8:]!r}:"
                f"results={[(artifact.kind, artifact.content.decode('utf-8', errors='replace')) for artifact in harness.store._artifacts.values() if artifact.kind in {'build_diagnostic', 'test_result'}]!r}:"
                f"serial={channel.diagnostic_tail()[-12:]!r}"
            )
        expected = {
            "search_files", "write_file", "apply_patch",
            "build_ucore_program", "run_ucore_program",
        }
        tool_records = [
            record for record in harness.tasks._tasks.values()
            if record.descriptor.operation_tool in expected
        ]
        observed = {record.descriptor.operation_tool for record in tool_records}
        if observed != expected or len(tool_records) != 7:
            raise RuntimeError(f"native_tool_trace_incomplete:{sorted(observed)}")
        if any(
            record.state != "terminal"
            or record.terminal_status != "ok"
            or record.result_artifact <= 0
            or record.native_context_sequence <= 0
            for record in tool_records
        ):
            raise RuntimeError("native_tool_terminal_evidence_invalid")
        artifacts = list(harness.store._artifacts.values())
        texts = [
            artifact.content.decode("utf-8", errors="strict")
            for artifact in artifacts
            if artifact.kind in {"search", "file", "patch", "build_diagnostic", "test_result"}
        ]
        failed_build = any(
            text.startswith("ucore_build\n") and _fields(text).get("status") == "failed"
            for text in texts
        )
        passed_builds = [
            _fields(text).get("build_id", "") for text in texts
            if text.startswith("ucore_build\n") and _fields(text).get("status") == "passed"
        ]
        passed_suite = any(
            text.startswith("ucore_run_suite\n")
            and _fields(text).get("status") == "passed"
            and _fields(text).get("passed_count") == "3"
            and _fields(text).get("independent_guest_count") == "3"
            for text in texts
        )
        indexed_read = any(
            text.startswith("catalog_evidence_v1\n")
            and _fields(text).get("used_index") == "1"
            and int(_fields(text).get("catalog_records", "0"))
            > int(_fields(text).get("catalog_candidates", "0"))
            for text in texts
        )
        rejected_search = any(
            text.startswith("workspace_tool_error\n")
            and _fields(text).get("status") == "rejected"
            and _fields(text).get("code") == "invalid_manifest_prefix"
            for text in texts
        )
        native_rows = [
            row for row in agent.private_context
            if row.get("kind") == "tool_result"
        ]
        if (
            not failed_build
            or not passed_builds
            or not passed_suite
            or not indexed_read
            or not rejected_search
            or len(native_rows) != 7
            or any(
                min(
                    int(row.get("native_artifact_sequence", 0)),
                    int(row.get("native_terminal_sequence", 0)),
                    int(row.get("native_merge_sequence", 0)),
                ) <= 0
                for row in native_rows
            )
        ):
            raise RuntimeError("native_repair_evidence_incomplete")
        status = channel.status()
        fence = channel.fence(root_task)
        print(
            "agentos-nexus-harness-integration: PASS "
            f"tasks={len(tool_records)} structured_rejection=1 indexed_read=1 "
            f"failed_build=1 patched=1 "
            f"build_id={passed_builds[-1][:16]} guests=3 "
            f"context={status['context_latest']} fence={fence['fence_sequence']}"
        )
        return 0
    finally:
        harness.close()
        events.close()
        source.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--qemu", default=dev.QEMU_BINARY)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    return integration(args.workspace, args.qemu, args.kernel, args.image)


if __name__ == "__main__":
    raise SystemExit(main())
