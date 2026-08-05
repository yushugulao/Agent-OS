#!/usr/bin/env python3
"""Mutation tests for the read epoch lazy-finalizer contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-read-epoch-lazy-finalizer.py"
FILES = (
    "os/bio.c",
    "os/bio.h",
    "os/file.c",
    "os/file.h",
    "os/fs.c",
    "os/syscall.c",
)


class ReadEpochLazyFinalizerTests(unittest.TestCase):
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

    def test_read_cannot_take_mutation_epoch(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:",
            "\tcase SYS_read:\n\tcase SYS_write:",
        )
        self.assert_rejected("pure inode reads still acquire")

    def test_read_cannot_bypass_io_admission(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_read:\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
        )
        self.assert_rejected("inode reads bypass block-I/O admission")

    def test_final_inode_must_retain_cleanup_token(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_prepare(owner,",
            "bio_cleanup_token_prepare_disabled(owner,",
        )
        self.assert_rejected("final inode lacks cleanup-token retention")

    def test_cleanup_token_must_precede_final_detach(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (f->type == FD_INODE) {",
            "\tf->ref = 0;\n\tif (f->type == FD_INODE) {",
        )
        self.assert_rejected("cleanup retention does not precede")

    def test_untrusted_owner_must_have_system_fallback(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_prepare(FS_OWNER_SYSTEM,",
            "bio_cleanup_token_prepare_disabled(FS_OWNER_SYSTEM,",
        )
        self.assert_rejected("system fallback")

    def test_drop_only_fast_path_is_mandatory(self) -> None:
        self.mutate(
            "os/file.c",
            "iput_drop_only(receipt->ip)",
            "iput_drop_only_disabled(receipt->ip)",
        )
        self.assert_rejected("ordinary final close bypasses")

    def test_cleanup_token_must_begin_before_iput(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_begin(&receipt->cleanup_token)",
            "bio_cleanup_token_begin_disabled(&receipt->cleanup_token)",
        )
        self.assert_rejected("does not activate retained I/O ownership")

    def test_cleanup_token_must_end_after_iput(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_end(&receipt->cleanup_token)",
            "bio_cleanup_token_end_disabled(&receipt->cleanup_token)",
        )
        self.assert_rejected("leaks active I/O ownership")

    def test_settlement_must_stay_outside_fs_gate(self) -> None:
        self.mutate(
            "os/file.c",
            "\t    fs_epoch_request_held())",
            "\t    0)",
        )
        self.assert_rejected("settlement ignores gate ownership")

    def test_io_lease_must_end_before_gate(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tsyscall_transaction_end_io(transaction);\n"
            "\t\tif (fs_epoch_request_begin() < 0)",
            "\t\tif (fs_epoch_request_begin() < 0)",
        )
        self.assert_rejected("violates FS-gate/BIO ordering")

    def test_gate_must_release_before_token_settlement(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tsyscall_transaction_end_io(transaction);\n"
            "\tif (final && receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT)",
            "\tif (final && receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT)",
        )
        self.assert_rejected("violates FS-gate/BIO ordering")


if __name__ == "__main__":
    unittest.main()
