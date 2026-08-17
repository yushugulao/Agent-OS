#!/usr/bin/env python3
"""Tests for Nexus Harness progress events and terminal rendering."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_harness_progress as progress


class _Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class HarnessProgressTests(unittest.TestCase):
    def test_plain_progress_and_trace_share_structured_events(self) -> None:
        clock = _Clock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.ndjson"
            bus = progress.HarnessEventBus(
                mode="plain",
                goal="compile a calculator",
                trace_path=trace,
                stream=output,
                clock=clock,
                wall_clock=lambda: 100.0,
            )
            bus.emit("qemu", "qemu_starting", "Starting Guest")
            bus.emit_context(1, {"sequence": 7, "kind": "heartbeat"})
            clock.advance(1.0)
            bus.emit(
                "kernel", "kernel_status", "Guest tick 100",
                tick=100, lifecycle_id=2, lifecycle_generation=1,
            )
            clock.advance(1.0)
            bus.emit(
                "kernel", "kernel_status", "Guest tick 200",
                tick=200, lifecycle_id=2, lifecycle_generation=1,
            )
            bus.close()
            rendered = output.getvalue()
            self.assertIn("[QEMU] Starting Guest", rendered)
            self.assertIn("[KERNEL] Guest tick 100", rendered)
            self.assertNotIn("Guest tick 200", rendered)
            self.assertNotIn("waiting for work", rendered.lower())
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["kind"], "heartbeat")
        self.assertEqual(rows[1]["sequence"], 7)
        self.assertEqual(rows[1]["agent_id"], 1)
        self.assertEqual(rows[-1]["progress_sequence"], 4)

    def test_dashboard_auto_mode_tracks_agent_task_and_kernel(self) -> None:
        clock = _Clock()
        output = _TTY()
        bus = progress.HarnessEventBus(
            mode="auto", goal="开发一个简易计算器", stream=output,
            clock=clock, wall_clock=lambda: 100.0,
        )
        self.assertEqual(bus.mode, "dashboard")
        bus.emit(
            "agent", "agent_spawned", "Agent 1 is running",
            agent_id=1, label="configured-agent", native_pid=7,
        )
        bus.emit(
            "harness", "artifact_sealed", "Artifact 1 sealed",
            artifact=1, artifact_count=1, bytes=64,
        )
        clock.advance(0.1)
        bus.emit(
            "task", "root_task_submitted", "Root Task 1 submitted",
            task_id=1, target_agent=1,
        )
        clock.advance(0.1)
        bus.emit(
            "model", "model_started", "Agent 1 started model round 1",
            agent_id=1, task_id=1, model_round=1,
        )
        clock.advance(0.1)
        bus.emit(
            "kernel", "kernel_status", "Guest tick 42",
            tick=42, lifecycle_id=3, lifecycle_generation=1,
            tasks_active=1, sq_depth=0, cq_depth=0,
            context_count=2, wait_sleep_count=1, scheduler_runnable=1,
            scheduler_vruntime=10, scheduler_virtual_deadline=20,
            scheduler_service_cycles=30,
        )
        bus.close()
        rendered = output.getvalue()
        self.assertIn("\x1b[?25l", rendered)
        self.assertIn("\x1b[?25h", rendered)
        self.assertIn("Nexus Harness", rendered)
        self.assertIn("WAITING_MODEL", rendered)
        self.assertIn("lifecycle=3/1", rendered)
        self.assertIn("Artifacts 1/64B", rendered)

    def test_ndjson_and_off_modes_do_not_mix_formats(self) -> None:
        ndjson = io.StringIO()
        bus = progress.HarnessEventBus(mode="ndjson", stream=ndjson)
        bus.emit("tool", "tool_started", "Tool started", tool="read_file")
        bus.close()
        row = json.loads(ndjson.getvalue())
        self.assertEqual(row["source"], "tool")
        self.assertEqual(row["tool"], "read_file")

        quiet = io.StringIO()
        bus = progress.HarnessEventBus(mode="off", stream=quiet)
        bus.emit("harness", "workflow_starting", "Starting")
        bus.close()
        self.assertEqual(quiet.getvalue(), "")

        bus = progress.HarnessEventBus(mode="off")
        with self.assertRaisesRegex(ValueError, "progress_source_invalid"):
            bus.emit("unknown", "invalid", "Invalid source")
        bus.close()


if __name__ == "__main__":
    raise SystemExit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
