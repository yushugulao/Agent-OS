#!/usr/bin/env python3
"""Directed mutation tests for the indexed system-file freelist."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-filepool-freelist.py"
FILES = ("os/file.c", "os/file.h", "os/proc.c")


class FilepoolFreelistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self):
        self.temporary.cleanup()

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def mutate(self, relative, old, new):
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self, message):
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_boot_cannot_skip_freelist_initialization(self):
        self.mutate("os/proc.c", "\tfilepool_init();", "\t/* omitted */")
        self.assert_rejected("filepool must initialize")

    def test_allocation_cannot_fall_back_to_a_pool_scan(self):
        self.mutate(
            "os/file.c",
            "static uint filepool_pop_locked(void)\n{\n"
            "\tuint16 index = filepool_allocator.free_head;",
            "static uint filepool_pop_locked(void)\n{\n"
            "\tuint16 index;\n\tfor (index = 0; index < FILEPOOLSIZE; index++);",
        )
        self.assert_rejected("must not scan the pool")

    def test_charge_must_precede_freelist_mutation(self):
        self.mutate(
            "os/file.c",
            "\t\tuint index = filepool_pop_locked();",
            "\t\tuint index = 0;",
        )
        self.assert_rejected("resource charging must succeed")

    def test_double_free_guard_is_required(self):
        path = self.root / "os/file.c"
        source = path.read_text(encoding="utf-8")
        marker = source.index("static void filepool_push_locked")
        tail = source[marker:]
        old = "filepool_allocator.slot_state[index] != FILEPOOL_SLOT_LIVE"
        self.assertIn(old, tail)
        tail = tail.replace(
            old,
            "filepool_allocator.slot_state[index] != FILEPOOL_SLOT_FREE",
            1,
        )
        path.write_text(source[:marker] + tail, encoding="utf-8")
        self.assert_rejected("free does not reject duplicate publication")

    def test_probe_bound_must_remain_constant(self):
        self.mutate(
            "os/file.c",
            "filepool_allocator.max_slot_pop_probes = 1;",
            "filepool_allocator.max_slot_pop_probes = FILEPOOLSIZE;",
        )
        self.assert_rejected("worst probe is not constant")

    def test_debug_invariant_must_detect_cycles(self):
        self.mutate("os/file.c", 'panic("filepool free cycle");', "break;")
        self.assert_rejected("freelist cycles")


if __name__ == "__main__":
    unittest.main()
