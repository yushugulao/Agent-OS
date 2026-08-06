#!/usr/bin/env python3
"""Mutation tests for stable read/write file transactions."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-syscall-file-transaction.py"


class SyscallFileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        target = self.root / "os/syscall.c"
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "os/syscall.c", target)

    def tearDown(self):
        self.temporary.cleanup()

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def mutate(self, old, new):
        path = self.root / "os/syscall.c"
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self, message):
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hot_setup_cannot_clear_the_cold_receipt(self):
        self.mutate(
            "\ttransaction->id = trapframe->a7;",
            "\tmemset(transaction, 0, sizeof(*transaction));\n"
            "\ttransaction->id = trapframe->a7;",
        )
        self.assert_rejected("clears the cold close receipt")

    def test_reopen_cannot_redirect_classification(self):
        self.mutate(
            "syscall_file_uses_disk(transaction->file);",
            "syscall_file_uses_disk(syscall_fd_pin(transaction->args[0]));",
        )
        self.assert_rejected("does not pin before classifying")

    def test_read_cannot_reacquire_the_fd(self):
        self.mutate(
            "static uint64 sys_read(struct file *f, int fd, uint64 va, uint64 len)\n"
            "{\n\tuint64 result;",
            "static uint64 sys_read(struct file *f, int fd, uint64 va, uint64 len)\n"
            "{\n\tf = fdget(fd);\n\tuint64 result;",
        )
        self.assert_rejected("sys_read reacquires")

    def test_dispatch_cannot_replace_the_admitted_file(self):
        self.mutate(
            "sys_write(transaction.file, (int)args[0],",
            "sys_write(syscall_fd_pin(args[0]), (int)args[0],",
        )
        self.assert_rejected("write execution does not consume")

    def test_final_reference_cannot_escape_the_epoch(self):
        self.mutate(
            "\t\tfinal = fileclose_prepare(transaction->file, receipt);",
            "\t\tfinal = 0; /* leaked transaction file */",
        )
        self.assert_rejected("last inode reference")

    def test_read_cannot_rejoin_the_mutation_epoch(self):
        self.mutate(
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:",
            "\tcase SYS_read:\n\tcase SYS_write:",
        )
        self.assert_rejected("pure read still acquires")

    def test_read_must_keep_block_io_admission(self):
        self.mutate(
            "\tcase SYS_read:\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
        )
        self.assert_rejected("read/write I/O admission")

    def test_close_cannot_use_a_pre_detach_guess(self):
        self.mutate(
            "\t\t\tclose_status = fdclose_prepare(\n"
            "\t\t\t\t(int)transaction->args[0],\n"
            "\t\t\t\t&transaction->close_receipt);",
            "\t\t\tclose_status = transaction->fd_uses_disk;",
        )
        self.assert_rejected("close does not atomically detach")


if __name__ == "__main__":
    unittest.main()
