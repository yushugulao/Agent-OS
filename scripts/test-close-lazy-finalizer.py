#!/usr/bin/env python3
"""Mutation tests for final-reference-only close admission."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-close-lazy-finalizer.py"
FILES = ("os/file.c", "os/proc.c", "os/proc.h", "os/syscall.c")


class CloseLazyFinalizerTests(unittest.TestCase):
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

    def test_nonfinal_drop_must_not_initialize_receipt(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (f->ref > 1) {",
            "\treceipt->type = FD_NONE;\n\tif (f->ref > 1) {",
        )
        self.assert_rejected("initializes cold receipt state")

    def test_fd_detach_must_release_the_captured_identity(self) -> None:
        self.mutate(
            "os/proc.c",
            "\tfinal = fileclose_prepare(f, receipt);",
            "\tfinal = 0;",
        )
        self.assert_rejected("does not release the captured identity")

    def test_fd_detach_must_reserve_finalizer_before_unpublish(self) -> None:
        self.mutate(
            "os/proc.c",
            "\tfinal = fileclose_prepare(f, receipt);\n",
            "\tp->files[fd] = 0;\n"
            "\tfinal = fileclose_prepare(f, receipt);\n",
        )
        self.assert_rejected("before its finalizer token")

    def test_close_cannot_take_unconditional_fs_epoch(self) -> None:
        path = self.root / "os/syscall.c"
        source = path.read_text(encoding="utf-8")
        marker = source.index("static int syscall_needs_fs_epoch")
        target = source.index("\tcase SYS_close:\n\t\treturn 0;", marker)
        source = source[:target] + source[target:].replace(
            "\tcase SYS_close:\n\t\treturn 0;",
            "\tcase SYS_close:\n\t\treturn 1;",
            1,
        )
        path.write_text(source, encoding="utf-8")
        self.assert_rejected("unconditional FS epoch")

    def test_close_cannot_take_unconditional_bio(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_close:\n\t\treturn 0;",
            "\tcase SYS_close:\n\t\treturn 1;",
        )
        self.assert_rejected("unconditional BIO")

    def test_nonfinal_close_must_return_before_admission(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tif (close_status <= 0)\n\t\t\treturn 0;",
            "\t\tif (close_status < 0)\n\t\t\treturn 0;",
        )
        self.assert_rejected("non-final close enters")

    def test_pipe_close_must_return_before_admission(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tif (transaction->close_receipt.type != FD_INODE)\n"
            "\t\t\treturn 0;",
            "\t\tif (0)\n\t\t\treturn 0;",
        )
        self.assert_rejected("pipe or stdio close enters")

    def test_final_flag_must_precede_admission_failure(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\ttransaction->close_final = 1;",
            "\t\ttransaction->close_final = 0;",
        )
        self.assert_rejected("final close receipt is not retained")

    def test_final_inode_must_not_prefetch_cleanup_admission(self) -> None:
        path = self.root / "os/syscall.c"
        source = path.read_text(encoding="utf-8")
        begin = source.index("static int syscall_transaction_begin")
        finish = source.index("static void syscall_transaction_end_io", begin)
        close_path = source[begin:finish]
        marker = (
            "\t\tif (transaction->close_receipt.type != FD_INODE)\n"
            "\t\t\treturn 0;"
        )
        self.assertIn(marker, close_path)
        close_path = close_path.replace(
            marker,
            marker + "\n\t\t(void)bio_request_begin_current_cleanup();",
            1,
        )
        path.write_text(source[:begin] + close_path + source[finish:],
                        encoding="utf-8")
        self.assert_rejected("eagerly reserves BIO capacity")

    def test_inode_close_must_try_drop_only_before_epoch(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tint dropped = fileclose_finish_drop_only(receipt);",
            "\t\tint dropped = 0;",
        )
        self.assert_rejected("admission-free path")

    def test_dispatch_cannot_detach_again(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tret = transaction.close_attempted ?\n"
            "\t\t\ttransaction.close_result : -1;",
            "\t\tret = fdclose((int)args[0]);",
        )
        self.assert_rejected("dispatch detaches or classifies")

    def test_settlement_must_select_transaction_receipt(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tstruct file_close_receipt *receipt ="
            " &transaction->close_receipt;",
            "\tstruct file_close_receipt *receipt = 0;",
        )
        self.assert_rejected("copied or unrelated receipt")


if __name__ == "__main__":
    unittest.main()
