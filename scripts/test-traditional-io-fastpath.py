#!/usr/bin/env python3
"""Mutation tests for the bounded traditional-I/O fast path."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-traditional-io-fastpath.py"
FILES = (
    "os/kernel_work.h",
    "os/kernel_work.c",
    "os/file.c",
    "os/pipe.c",
    "os/syscall.c",
)


class TraditionalIoFastPathTests(unittest.TestCase):
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

    def assert_rejected(self, needle: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_single_block_batch(self) -> None:
        self.mutate(
            "os/kernel_work.h",
            "#define KERNEL_WORK_IO_BATCH_BYTES (16U * 1024U)",
            "#define KERNEL_WORK_IO_BATCH_BYTES BSIZE",
        )
        self.assert_rejected("16 KiB batch")

    def test_rejects_floor_byte_normalization(self) -> None:
        self.mutate(
            "os/kernel_work.c",
            "if (bytes % KERNEL_WORK_BYTES_PER_UNIT != 0)\n\t\tunits++;",
            "if (0)\n\t\tunits++;",
        )
        self.assert_rejected("ceil(bytes / 64)")

    def test_rejects_unbounded_normalization(self) -> None:
        self.mutate(
            "os/kernel_work.c",
            "if (units > KERNEL_WORK_BUDGET_UNITS)\n\t\treturn KERNEL_WORK_BUDGET_UNITS;",
            "if (0)\n\t\treturn KERNEL_WORK_BUDGET_UNITS;",
        )
        self.assert_rejected("not saturating")

    def test_rejects_inode_one_block_loop(self) -> None:
        self.mutate(
            "os/file.c",
            "chunk = inode_io_transaction_batch(&transaction);",
            "chunk = BSIZE - offset % BSIZE;",
        )
        self.assert_rejected("bypasses the bounded batch")

    def test_rejects_per_batch_credential_rebuild(self) -> None:
        self.mutate(
            "os/file.c",
            "r = readi_lease(transaction.inode, &transaction.cred,\n"
            "\t\t\t\t&transaction.lease, 1, user_addr,",
            "vfs_cred_from_proc(curr_proc(), &transaction.cred);\n"
            "\t\tr = readi_lease(transaction.inode, &transaction.cred,\n"
            "\t\t\t\t&transaction.lease, 1, user_addr,",
        )
        self.assert_rejected("rebuilds the credential inside the batch loop")

    def test_rejects_raw_byte_work_charge(self) -> None:
        self.mutate(
            "os/file.c",
            "checkpoint = kernel_work_checkpoint_bytes((uint)r);",
            "checkpoint = kernel_work_checkpoint((uint)r);",
        )
        self.assert_rejected("does not normalize byte work")

    def test_rejects_second_descriptor_classification(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_read:\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
            "\tcase SYS_read:\n\tcase SYS_write:\n"
            "return syscall_fd_uses_disk(args[0]);",
        )
        self.assert_rejected("read/write I/O admission rescans")

    def test_rejects_io_before_epoch_admission(self) -> None:
        path = self.root / "os/syscall.c"
        source = path.read_text(encoding="utf-8")
        epoch = source.index("\tif (syscall_needs_fs_epoch(transaction)) {")
        io = source.index("\tif (syscall_may_issue_block_io(transaction)) {")
        epoch_block = source[epoch:io]
        io_end = source.index("\treturn 0;", io)
        io_block = source[io:io_end]
        path.write_text(
            source[:epoch] + io_block + epoch_block + source[io_end:],
            encoding="utf-8",
        )
        self.assert_rejected("admits I/O before its epoch")

    def test_rejects_lost_cycle_deadline(self) -> None:
        self.mutate(
            "os/kernel_work.c",
            "now < t->kernel_slice_deadline",
            "1",
        )
        self.assert_rejected("dispatch deadline")


if __name__ == "__main__":
    unittest.main()
