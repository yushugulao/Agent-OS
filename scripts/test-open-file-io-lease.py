#!/usr/bin/env python3
"""Mutation tests for the trusted open-file authorization context."""

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
            [sys.executable, "-X", "utf8", "-I", "-S", "-B", str(CHECKER),
             "--root", str(self.root)],
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

    def test_rejects_opaque_token(self) -> None:
        self.mutate("os/open_file_io_lease.h", "struct file *file;",
                    "uint64 opaque[2];")
        self.assert_rejected("typed syscall authority")

    def test_rejects_crypto_tax(self) -> None:
        self.mutate("os/open_file_io_lease.c",
                    "static struct open_file_io_state open_file_io_state;",
                    "static struct open_file_io_state open_file_io_state;\n"
                    "static uint64 token_secret;")
        self.assert_rejected("cryptographic sealing tax")

    def test_rejects_duplicate_file_generation(self) -> None:
        self.mutate("os/open_file_io_lease.c",
                    "struct open_file_io_lease_stats stats;",
                    "uint file_generations[FILEPOOLSIZE];\n\t"
                    "struct open_file_io_lease_stats stats;")
        self.assert_rejected("duplicate lifetime protocol")

    def test_rejects_full_auth_before_fast_path(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "authorized = vfs_inode_authorize(candidate.inode, &candidate.cred,\n"
            "\t\t\t\t\t operation);",
            "authorized = vfs_inode_authorize(candidate.inode, &candidate.cred, operation) &&\n"
            "\t\tvfs_inode_authorize(candidate.inode, &candidate.cred, operation);",
        )
        self.assert_rejected("exactly one full VFS authorization")

    def test_rejects_missing_slow_revalidation(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "if (!authorized ||\n\t    !open_file_io_grant_matches(&candidate, file, proc, operation))",
            "if (!authorized)",
        )
        self.assert_rejected("not revalidated")

    def test_rejects_missing_fs_validation(self) -> None:
        self.mutate("os/fs.c",
                    "open_file_io_token_validate(lease, ip, VFS_OP_READ)", "1")
        self.assert_rejected("filesystem does not verify")

    def test_rejects_full_authorization_at_fs_boundary(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "valid = token->subject == proc && token->inode == inode &&",
            "valid = vfs_inode_authorize(inode, &token->cred, operation) &&\n"
            "\t\ttoken->subject == proc && token->inode == inode &&",
        )
        self.assert_rejected("repeats full VFS authorization")

    def test_rejects_cache_residency_dependency(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "valid = token->subject == proc && token->inode == inode &&",
            "valid = open_file_io_state.grants[0].valid &&\n"
            "\t\ttoken->subject == proc && token->inode == inode &&",
        )
        self.assert_rejected("cache residency")

    def test_rejects_thread_generation_bypass(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "thread->identity_generation == token->thread_generation &&",
            "token->thread_generation != 0 &&",
        )
        self.assert_rejected("revocation validation")

    def test_rejects_lifecycle_bypass(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "!open_file_io_lifecycle_live(current_lifecycle)", "0")
        self.assert_rejected("subject generation checks")

    def test_rejects_inode_generation_bypass(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "incarnation == inode->vfs_incarnation",
            "incarnation == incarnation",
        )
        self.assert_rejected("inode security identity")

    def test_rejects_edit_generation_bypass(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "authority_generation == current_generation &&",
            "authority_generation != 0 &&",
        )
        self.assert_rejected("edit revocation generation")

    def test_rejects_missing_open_authorization_seed(self) -> None:
        self.mutate("os/file.c",
                    "open_file_io_lease_seed_authorized(f, VFS_OP_READ, &cred);",
                    "(void)f;")
        self.assert_rejected("publish its completed read/write authorization")

    def test_rejects_read_write_cache_alias(self) -> None:
        self.mutate(
            "os/open_file_io_lease.c",
            "open_file_io_operation_mask(operation) *\n\t       0x9e3779b97f4a7c15ULL",
            "open_file_io_operation_mask(operation) << 8",
        )
        self.assert_rejected("cache key")

    def test_rejects_global_edit_generation(self) -> None:
        self.mutate("os/agent_file_state.c", "uint64 edit_authority_generation;",
                    "uint64 unrelated_generation;")
        self.assert_rejected("inode-scoped revocation generation")

    def test_rejects_cold_rebuild_authority_aba(self) -> None:
        self.mutate(
            "os/agent_file_state.c",
            "entry->edit_authority_generation =\n"
            "\t\tagent_file_edit_authority_generation;",
            "entry->edit_authority_generation = 0;",
        )
        self.assert_rejected("inode-scoped revocation generation")

    def test_rejects_cross_inode_global_comparison(self) -> None:
        self.mutate(
            "os/agent_file_state.c",
            "\t\t*authority_generation = version->edit_authority_generation;",
            "\t\t*authority_generation = agent_file_edit_authority_generation;",
        )
        self.assert_rejected("inode-scoped revocation generation")

    def test_rejects_snapshot_conflict_side_effects(self) -> None:
        self.mutate(
            "os/agent_file_state.c",
            "\t\tip, 0, authority_generation, valid_until_tick);",
            "\t\tip, \"edit_snapshot_conflict\", authority_generation,\n"
            "\t\tvalid_until_tick);",
        )
        self.assert_rejected("side-effect-free delegation")

    def test_rejects_unguarded_snapshot_conflict_count(self) -> None:
        self.mutate("os/agent_file_state.c", "\t\tif (action) {", "\t\tif (1) {")
        self.assert_rejected("no longer suppresses audit side effects")


if __name__ == "__main__":
    unittest.main()
