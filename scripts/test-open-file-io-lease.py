#!/usr/bin/env python3
"""Mutation tests for the open-file authorization lease."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-open-file-io-lease.py"


class OpenFileIoLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "os/open_file_io_lease.h", "os/open_file_io_lease.c", "os/file.c",
            "os/fs.c", "os/agent_file_state.c",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(CHECKER), "--root", str(self.root)],
            text=True, capture_output=True, check=False,
        )

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text()
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1))

    def assert_rejected(self, message: str) -> None:
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_large_token(self) -> None:
        self.mutate("os/open_file_io_lease.h", "opaque[4]", "opaque[8]")
        self.assert_rejected("four words")

    def test_rejects_slot_aba(self) -> None:
        self.mutate("os/open_file_io_lease.c",
                    "open_file_io_next32(&open_file_io_state.file_generations[slot]);",
                    "open_file_io_state.file_generations[slot];")
        self.assert_rejected("generation is not advanced")

    def test_rejects_full_auth_before_fast_path(self) -> None:
        path = self.root / "os/open_file_io_lease.c"
        source = path.read_text()
        source = source.replace("vfs_inode_authorize(candidate.inode, &candidate.cred,\n\t\t\t\t\t operation)",
                                "vfs_inode_authorize(candidate.inode, &candidate.cred, operation) &&\n\t\tvfs_inode_authorize(candidate.inode, &candidate.cred, operation)", 1)
        path.write_text(source)
        self.assert_rejected("exactly one full VFS authorization")

    def test_rejects_missing_fs_validation(self) -> None:
        self.mutate("os/fs.c", "open_file_io_token_validate(lease, ip, VFS_OP_READ)", "1")
        self.assert_rejected("filesystem does not verify")

    def test_rejects_full_authorization_walk_at_fs_boundary(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "if (open_file_io_grant_matches_locked(\n\t\t    &current, inode, proc, operation) &&",
            "if (vfs_inode_authorize(inode, &current.cred, operation) &&\n\t    open_file_io_grant_matches_locked(\n\t\t    &current, inode, proc, operation) &&",
        )
        self.assert_rejected("repeats full VFS authorization")

    def test_rejects_missing_revocation_check_after_blocking(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "open_file_io_grant_matches_locked(\n\t\t    &current, inode, proc, operation) &&",
            "current.valid &&",
        )
        self.assert_rejected("filesystem token validation is incomplete")

    def test_rejects_token_dependency_on_cache_residency(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "if (open_file_io_grant_matches_locked(\n\t\t    &current, inode, proc, operation) &&",
            "if (open_file_io_state.grants[0].valid &&\n\t    open_file_io_grant_matches_locked(\n\t\t    &current, inode, proc, operation) &&",
        )
        self.assert_rejected("grant-cache residency")

    def test_rejects_missing_open_authorization_seed(self) -> None:
        self.mutate(
            "os/file.c",
            "open_file_io_lease_seed_authorized(f, VFS_OP_READ, &cred);",
            "(void)f;",
        )
        self.assert_rejected("publish its completed read/write authorization")

    def test_rejects_file_slot_aba_bypass(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "generation == open_file_io_state.file_generations[slot]",
            "generation == generation",
        )
        self.assert_rejected("file-slot ABA")

    def test_rejects_short_lived_file_slot_cache_key(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "cache_slot = open_file_io_cache_slot(file->ip, proc, operation);",
            "cache_slot = file_slot & (OPEN_FILE_IO_CACHE_CAP - 1);",
        )
        self.assert_rejected("short-lived file slots")

    def test_rejects_read_write_cache_alias(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "open_file_io_operation_mask(operation) *\n\t       0x9e3779b97f4a7c15ULL",
            "open_file_io_operation_mask(operation) << 8",
        )
        self.assert_rejected("read and write grants collide")

    def test_rejects_missing_stable_inode_identity(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "grant->inode_inum == inode->inum",
            "grant->inode_inum == grant->inode_inum",
        )
        self.assert_rejected("stable inode security identity")

    def test_rejects_global_edit_authority_generation(self) -> None:
        self.mutate(
            "os/agent_file_state.c",
            "uint64 edit_authority_generation;",
            "uint64 unrelated_generation;",
        )
        self.assert_rejected("inode-scoped revocation generation")

    def test_rejects_edit_generation_on_read_grants(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "grant->edit_authority_generation != 0 ||",
            "0 ||",
        )
        self.assert_rejected("global edit invalidation")

    def test_rejects_unbounded_lifetime_hook(self) -> None:
        self.mutate("os/open_file_io_lease.c", "\topen_file_io_next32(&open_file_io_state.file_generations[slot]);",
                    "\tfor (;;) break;\n\topen_file_io_next32(&open_file_io_state.file_generations[slot]);")
        self.assert_rejected("no longer O(1)")


if __name__ == "__main__":
    unittest.main()
