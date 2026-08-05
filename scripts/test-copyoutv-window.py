#!/usr/bin/env python3
"""Mutation tests for the bounded scatter-copyout VM window."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-copyoutv-window.py"
FILES = ("os/vm.c", "os/vm.h")


class CopyoutvWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_rejected(self, needle: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_eight_segment_stack_expansion(self) -> None:
        self.mutate("os/vm.h", "#define VM_COPY_SEGMENT_MAX 4U", "#define VM_COPY_SEGMENT_MAX 8U")
        self.assert_rejected("segment bound is not four")

    def test_rejects_unbounded_total(self) -> None:
        self.mutate(
            "os/vm.c",
            "if (length > VM_COPYOUTV_MAX_BYTES - total)",
            "if (0)",
        )
        self.assert_rejected("total is not overflow-safe")

    def test_rejects_source_overflow(self) -> None:
        self.mutate(
            "os/vm.c",
            "source == 0 || length > (uint64)-1 - source",
            "source == 0",
        )
        self.assert_rejected("source address overflow")

    def test_rejects_foreign_pagetable(self) -> None:
        self.mutate("os/vm.c", "p->pagetable != pagetable ||", "0 ||")
        self.assert_rejected("one current-process page table")

    def test_rejects_missing_snapshot(self) -> None:
        self.mutate(
            "os/vm.c",
            "proc_vm_snapshot_begin(p) < 0",
            "0",
        )
        self.assert_rejected("page table snapshot")

    def test_rejects_post_write_cow(self) -> None:
        self.mutate(
            "os/vm.c",
            "if ((*pte & PTE_COW) != 0) {",
            "if (0) {",
        )
        self.assert_rejected("promote every COW page")

    def test_rejects_per_segment_copyout(self) -> None:
        self.mutate(
            "os/vm.c",
            "memmove((void *)(PTE2PA(*leaves[page_index]) + offset), source,",
            "copyout(pagetable, cursor, (char *)source,",
        )
        self.assert_rejected("does not reuse its prepared leaf pages")

    def test_rejects_kernel_destination_overflow(self) -> None:
        self.mutate(
            "os/vm.c",
            "if (dst == 0 || total > (uint64)-1 - dst)",
            "if (dst == 0)",
        )
        self.assert_rejected("kernel scatter destination overflow")


if __name__ == "__main__":
    unittest.main()
