#!/usr/bin/env python3
"""Mutation tests for the metadata query read-view contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-agent-metadata-read-view.py"
FILES = (
    "os/agent_metadata_catalog.c",
    "os/agent_metadata_catalog.h",
    "os/agent_metadata_query.c",
    "os/agent_metadata_objects.c",
)


class AgentMetadataReadViewTests(unittest.TestCase):
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

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_lost_lifecycle_capture(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "vfs_scope_lifecycle(scope_id, &lifecycle) < 0",
            "workflow_lifecycle_key_valid(lifecycle) == 0",
        )
        self.assert_rejected("trusted lifecycle capture")

    def test_rejects_unfiltered_candidate_bitmap(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "candidates[word] & agent_catalog_ready_bits[word]",
            "candidates[word]",
        )
        self.assert_rejected("ready candidate snapshot")

    def test_rejects_generation_copy_fence(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "snapshot->generation != agent_catalog_generation) {",
            "snapshot->generation == 0) {",
        )
        self.assert_rejected("generation fence")

    def test_rejects_lifecycle_publish_fence(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "vfs_scope_lifecycle(snapshot->scope_id, &lifecycle) == 0",
            "snapshot->scope_id == 0",
        )
        self.assert_rejected("revalidate lifecycle")

    def test_rejects_global_query_gate(self) -> None:
        self.mutate(
            "os/agent_metadata_query.c",
            "agent_query_result_reset(r, slots);",
            "agent_metadata_txn_lock(1);\n\tagent_query_result_reset(r, slots);",
        )
        self.assert_rejected("snapshot query entered metadata gate")

    def test_rejects_lost_scheduler_checkpoint(self) -> None:
        self.mutate(
            "os/agent_metadata_query.c",
            "kernel_work_checkpoint_cleanup(\n\t\t\t\tKERNEL_WORK_OPERATION_UNITS)",
            "0",
        )
        self.assert_rejected("bounded scheduler checkpoint")

    def test_rejects_unbounded_syscall_gate(self) -> None:
        self.mutate(
            "os/agent_metadata_objects.c",
            "returned = agent_file_query_internal",
            "agent_metadata_txn_lock(1);\n\treturned = agent_file_query_internal",
        )
        self.assert_rejected("query syscall still holds")

    def test_rejects_read_view_unlock(self) -> None:
        self.mutate(
            "os/agent_metadata_objects.c",
            "if (locked == 1)",
            "if (locked > 0)",
        )
        self.assert_rejected("mistaken for an owned transaction")


if __name__ == "__main__":
    unittest.main()
