#!/usr/bin/env python3
"""Mutation tests for the tracked content metadata fast path."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-agent-metadata-content-fastpath.py"
FILES = (
    "os/agent_file_state.c",
    "os/agent_file_state_internal.h",
    "os/agent_metadata_directory.c",
    "os/agent_metadata_directory.h",
    "os/agent_metadata_catalog.c",
    "os/agent_metadata_catalog.h",
    "os/agent_metadata_store.c",
)


class AgentMetadataContentFastPathTests(unittest.TestCase):
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

    def mutate_file(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def mutate(self, old: str, new: str) -> None:
        self.mutate_file("os/agent_metadata_directory.c", old, new)

    def mutate_catalog(self, old: str, new: str) -> None:
        path = self.root / "os/agent_metadata_catalog.c"
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_catalog_work_for_every_overwrite(self) -> None:
        self.mutate(
            "if (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
            "agent_metadata_catalog_borrow(0, 0, 0);\n"
            "\tif (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
        )
        self.assert_rejected("enters catalog transaction work")

    def test_rejects_transaction_lock_for_every_overwrite(self) -> None:
        self.mutate(
            "if (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
            "agent_metadata_txn_try_external();\n"
            "\tif (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
        )
        self.assert_rejected("enters catalog transaction work")

    def test_rejects_lost_content_version(self) -> None:
        self.mutate_file(
            "os/agent_file_state.c",
            "entry->content_version =\n"
            "\t\t\tagent_file_counter_next(&agent_file_content_generation);\n"
            "\t\tentry->published_size_valid = 1;",
            "entry->content_version = 1;\n"
            "\t\tentry->published_size_valid = 1;",
        )
        self.assert_rejected("does not bump content generation")

    def test_rejects_lost_size_overlay(self) -> None:
        self.mutate_file(
            "os/agent_file_state.c",
            "entry->published_size = ip->size;",
            "entry->published_size = 0;",
        )
        self.assert_rejected("lost inode size snapshot")

    def test_rejects_split_file_state_lock_transactions(self) -> None:
        self.mutate_file(
            "os/agent_file_state.c",
            "if (ip == 0)\n"
            "\t\treturn 0;\n"
            "\tenabled = agent_edit_lock();\n"
            "\tentry = file_version_inode_locked(ip, 1);",
            "if (ip == 0)\n"
            "\t\treturn 0;\n"
            "\tenabled = agent_edit_lock();\n"
            "\tagent_edit_unlock(enabled);\n"
            "\tenabled = agent_edit_lock();\n"
            "\tentry = file_version_inode_locked(ip, 1);",
        )
        self.assert_rejected("not one edit-lock transaction")

    def test_rejects_split_content_hook(self) -> None:
        self.mutate(
            "if (!agent_file_state_content_publish(ip, &receipt) ||",
            "agent_file_state_content_bump(ip);\n"
            "\tif (!agent_file_state_content_publish(ip, &receipt) ||",
        )
        self.assert_rejected("retained a separate version transaction")

    def test_rejects_unconditional_persistence_work(self) -> None:
        self.mutate(
            "if (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST) {\n"
            "\t\tif (agent_metadata_catalog_journal_note_content(&receipt) < 0)\n"
            "\t\t\treconcile = 1;\n"
            "\t\tagent_metadata_store_mark_dirty(ip->vfs_scope_id);\n"
            "\t}",
            "agent_metadata_store_mark_dirty(ip->vfs_scope_id);",
        )
        self.assert_rejected("persistent content")

    def test_rejects_synchronous_summary_rewrite(self) -> None:
        self.mutate(
            "if (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
            "meta->summary[0] = 0;\n"
            "\tif (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST)",
        )
        self.assert_rejected("rewrites summary text")

    def test_rejects_direct_write_catalog_path(self) -> None:
        self.mutate(
            "agent_fs_publish_content(ip);\n}\n\nvoid agent_fs_note_truncate",
            "agent_fs_remove_inode(ip);\n}\n\nvoid agent_fs_note_truncate",
        )
        self.assert_rejected("bypasses the shared content fast path")

    def test_rejects_lost_anomaly_reconciliation(self) -> None:
        self.mutate(
            "ip->agent_meta_version != AGENT_INODE_META_VERSION",
            "0",
        )
        self.assert_rejected("stale metadata sidecar")

    def test_rejects_sidecar_without_autoscan(self) -> None:
        self.mutate_catalog(
            "meta->flags & (AGENT_FILE_META_F_PERSIST |\n"
            "\t\t\t       AGENT_FILE_META_F_AUTOSCAN)",
            "meta->flags & AGENT_FILE_META_F_PERSIST",
        )
        self.assert_rejected("drops a hot-path metadata flag")

    def test_rejects_lost_exact_content_receipt(self) -> None:
        self.mutate(
            "agent_metadata_catalog_journal_note_content(&receipt)",
            "agent_metadata_catalog_journal_note_content(0)",
        )
        self.assert_rejected("does not enqueue one exact receipt")

    def test_rejects_scope_wide_journal_settlement(self) -> None:
        self.mutate_file(
            "os/agent_metadata_store.c",
            "agent_file_writeback_note_journal(\n"
            "\t\tagent_meta_persist.scope_id, 0, 1);\n"
            "\tagent_file_writeback_advance(0);\n"
            "\tagent_durable_section_commit_scope(\n"
            "\t\tagent_meta_persist.scope_id,\n"
            "\t\tagent_meta_persist.durable_serial);",
            "agent_file_writeback_note_journal(\n"
            "\t\tagent_meta_persist.scope_id, 0, 1);\n"
            "\tagent_file_state_sizes_persisted(\n"
            "\t\tagent_meta_persist.scope_id,\n"
            "\t\tagent_meta_persist.size_sequence);\n"
            "\tagent_file_writeback_advance(0);\n"
            "\tagent_durable_section_commit_scope(\n"
            "\t\tagent_meta_persist.scope_id,\n"
            "\t\tagent_meta_persist.durable_serial);",
        )
        self.assert_rejected("clears uncaptured scope-wide overlays")

    def test_rejects_non_exact_overlay_settlement(self) -> None:
        self.mutate_catalog(
            "agent_file_state_content_settle(\n"
            "\t\t\t&receipt->changes[captured].content);",
            "agent_file_state_sizes_persisted(receipt->scope_id, ~0ULL);",
        )
        self.assert_rejected("does not settle the exact captured overlay")


if __name__ == "__main__":
    unittest.main()
