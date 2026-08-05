#!/usr/bin/env python3
"""Mutation tests for event-driven background maintenance dispatch."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-background-dispatch-fastpath.py"
FILES = ("os/syscall.c", "os/trap.c", "os/proc.c")


class BackgroundDispatchFastPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_ordinary_syscall_maintenance(self) -> None:
        self.mutate(
            "os/syscall.c",
            "if (id == SYS_sched_yield)\n\t\tagent_background_checkpoint();",
            "if (id != SYS_agent_performance_snapshot)\n"
            "\t\tagent_background_checkpoint();",
        )
        self.assertNotEqual(self.run_checker().returncode, 0)

    def test_rejects_missing_timer_progress(self) -> None:
        self.mutate("os/trap.c", "\t\tagent_background_checkpoint();\n", "")
        self.assertNotEqual(self.run_checker().returncode, 0)

    def test_rejects_missing_idle_writeback(self) -> None:
        self.mutate(
            "os/proc.c",
            "if (t == NULL && fs_epoch_should_commit() &&",
            "if (0 && fs_epoch_should_commit() &&",
        )
        self.assertNotEqual(self.run_checker().returncode, 0)


if __name__ == "__main__":
    unittest.main()
