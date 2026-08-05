#!/usr/bin/env python3
"""Mutation tests for sparse Sv39 fork and teardown traversal."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-vm-page-table-fastpath.py"


class VmPageTableFastpathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        target = self.root / "os/vm.c"
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "os/vm.c", target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutate(self, old: str, new: str) -> None:
        path = self.root / "os/vm.c"
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

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_teardown_cannot_restore_dense_scan(self) -> None:
        self.mutate(
            "uvm_release_range_tree(pagetable, max_page * PGSIZE, 0);",
            "uvmunmap(pagetable, 0, max_page, 1);",
        )
        self.assert_rejected("uvmfree does not use the allocated page-table tree")

    def test_clone_must_retain_shared_leaf(self) -> None:
        self.mutate(
            "if (kretain_account_page(\n"
            "\t\t\t\t\t    (void *)PTE2PA(source)) < 0)",
            "if (0)",
        )
        self.assert_rejected("fork does not retain shared leaf ownership")

    def test_clone_cannot_mutate_parent_early(self) -> None:
        self.mutate(
            "new_l0[l0_slot] =\n\t\t\t\t\tPA2PTE(PTE2PA(source)) | flags;",
            "parent_l0[l0_slot] =\n\t\t\t\t\tPA2PTE(PTE2PA(source)) | flags;",
        )
        self.assert_rejected("fork does not install the validated leaf directly")

    def test_clone_must_validate_cow_permissions(self) -> None:
        self.mutate(
            "!uvmcopy_leaf_flags_valid(flags)",
            "0",
        )
        self.assert_rejected("fork bypasses leaf permission validation")

    def test_commit_must_match_physical_identity(self) -> None:
        self.mutate(
            "PTE2PA(parent) != PTE2PA(child)",
            "PTE2PA(parent) == PTE2PA(child)",
        )
        self.assert_rejected("does not bind parent and child leaf identity")

    def test_destination_range_must_be_empty(self) -> None:
        self.mutate(
            "if ((new[i] & PTE_V) != 0)\n\t\t\treturn -1;",
            "if (0)\n\t\t\treturn -1;",
        )
        self.assert_rejected("does not reserve an empty destination range")

    def test_parent_commit_must_follow_complete_clone(self) -> None:
        self.mutate(
            "if (uvmcopy_clone_tree(&state, old, new, limit) < 0) {",
            "uvmcopy_commit_parent(old, new, limit);\n"
            "\tif (uvmcopy_clone_tree(&state, old, new, limit) < 0) {",
        )
        self.assert_rejected("publication order no longer preserves failure atomicity")


if __name__ == "__main__":
    unittest.main()
