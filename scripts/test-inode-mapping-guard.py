#!/usr/bin/env python3
"""Mutation tests for inode mapping and shared-offset guards."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-inode-mapping-guard.py"
FILES = ("os/fs.c", "os/file.c")


class InodeMappingGuardTests(unittest.TestCase):
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

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_guard_must_cover_buffer_release(self) -> None:
        path = self.root / "os/fs.c"
        source = path.read_text(encoding="utf-8")
        start = source.index("static int readi_with_auth")
        prefix, body = source[:start], source[start:]
        old = (
            "\tresult = readi_atomic(ip, cred, lease, user_dst, dst, off, n, 0);\n"
            "\tinode_mapping_read_unlock(ip);"
        )
        self.assertIn(old, body)
        body = body.replace(
            old,
            "\tinode_mapping_read_unlock(ip);\n"
            "\tresult = readi_atomic(ip, cred, lease, user_dst, dst, off, n, 0);",
            1,
        )
        path.write_text(prefix + body, encoding="utf-8")
        self.assert_rejected("retain the mapping read guard")

    def test_bmap_allocation_requires_writer(self) -> None:
        self.mutate(
            "os/fs.c",
            "\tinode_mapping_require(ip, alloc != 0);",
            "\tinode_mapping_require(ip, 0);",
        )
        self.assert_rejected("bmap does not enforce")

    def test_truncate_must_take_writer(self) -> None:
        path = self.root / "os/fs.c"
        source = path.read_text(encoding="utf-8")
        start = source.index("int itruncate_detach")
        prefix, body = source[:start], source[start:]
        old = "\tif (inode_mapping_write_lock(ip, 0) < 0)\n\t\treturn -1;"
        self.assertIn(old, body)
        body = body.replace(old, "\tif (0)\n\t\treturn -1;", 1)
        path.write_text(prefix + body, encoding="utf-8")
        self.assert_rejected("truncate does not publish")

    def test_final_detach_must_be_uninterruptible(self) -> None:
        marker = "int inode_remove_detach"
        path = self.root / "os/fs.c"
        source = path.read_text(encoding="utf-8")
        start = source.index(marker)
        prefix, body = source[:start], source[start:]
        self.assertIn("inode_mapping_write_lock(ip, 1)", body)
        body = body.replace("inode_mapping_write_lock(ip, 1)",
                            "inode_mapping_write_lock(ip, 0)")
        path.write_text(prefix + body, encoding="utf-8")
        self.assert_rejected("uninterruptible mapping writer")

    def test_directory_scan_must_hold_reader_through_brelse(self) -> None:
        self.mutate(
            "os/fs.c",
            "\tif (bp != 0)\n\t\tbrelse(bp);\n"
            "\tinode_mapping_read_unlock(dp);",
            "\tinode_mapping_read_unlock(dp);\n\tif (bp != 0)\n"
            "\t\tbrelse(bp);",
        )
        self.assert_rejected("directory scan releases")

    def test_offset_guard_must_cover_write_offset(self) -> None:
        marker = "uint64 inodewrite"
        path = self.root / "os/file.c"
        source = path.read_text(encoding="utf-8")
        start = source.index(marker)
        prefix, body = source[:start], source[start:]
        self.assertIn("\tfile_offset_unlock(f);", body)
        body = body.replace("\tfile_offset_unlock(f);", "\t/* omitted */;", 1)
        path.write_text(prefix + body, encoding="utf-8")
        self.assert_rejected("inodewrite does not serialize")

    def test_offset_guard_must_cover_read_offset(self) -> None:
        marker = "uint64 inoderead"
        path = self.root / "os/file.c"
        source = path.read_text(encoding="utf-8")
        start = source.index(marker)
        prefix, body = source[:start], source[start:]
        self.assertIn("\tif (file_offset_lock(f) < 0)", body)
        body = body.replace("\tif (file_offset_lock(f) < 0)", "\tif (0)", 1)
        path.write_text(prefix + body, encoding="utf-8")
        self.assert_rejected("inoderead does not serialize")

    def test_slot_reuse_must_reject_offset_owner(self) -> None:
        self.mutate(
            "os/file.c",
            "\t    filepool_allocator.offset_busy[index] != 0 ||\n",
            "",
        )
        self.assert_rejected("slot can be recycled")


if __name__ == "__main__":
    unittest.main()
