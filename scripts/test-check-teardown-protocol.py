#!/usr/bin/env python3
"""Negative regression tests for the workflow teardown source contract."""

import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-teardown-protocol.py")
ROOT = SCRIPT.parent.parent
SPEC = importlib.util.spec_from_file_location("teardown_protocol", SCRIPT)
teardown_protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teardown_protocol)


class TeardownProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = teardown_protocol.load_sources(ROOT)

    def changed(self, key, old, new):
        sources = copy.deepcopy(self.good)
        self.assertEqual(sources[key].count(old), 1, f"mutation anchor drift: {old}")
        sources[key] = sources[key].replace(old, new, 1)
        return sources

    def assert_rejected(self, sources, pattern):
        with self.assertRaisesRegex(teardown_protocol.ProtocolError, pattern):
            teardown_protocol.validate_protocol(sources)

    def test_repository_protocol_is_valid(self):
        teardown_protocol.validate_protocol(copy.deepcopy(self.good))

    def test_wrong_phase_transition_is_rejected(self):
        sources = self.changed(
            "vfs",
            "scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_METADATA,\n"
            "\t\t\tmetadata_target",
            "scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_RETIRE,\n"
            "\t\t\tmetadata_target",
        )
        self.assert_rejected(sources, "FILES must advance once to METADATA")

    def test_duplicate_dirty_generation_is_rejected(self):
        assignment = (
            "\t\t*metadata_target = "
            "agent_metadata_store_mark_dirty(scope_id);"
        )
        sources = self.changed(
            "objects", assignment, assignment + "\n" + assignment
        )
        self.assert_rejected(sources, "mark_dirty exactly 1|dirty generation")

    def test_pending_files_cannot_advance(self):
        sources = self.changed(
            "vfs",
            "\t\tif (status == FS_RECLAIM_PENDING)\n\t\t\treturn;",
            "\t\tif (status == FS_RECLAIM_PENDING) {\n"
            "\t\t\t(void)vfs_scope_reclaim_advance(\n"
            "\t\t\t\tscope_id, lifecycle, phase,\n"
            "\t\t\t\tVFS_SCOPE_RECLAIM_METADATA, metadata_target);\n"
            "\t\t\treturn;\n\t\t}",
        )
        self.assert_rejected(sources, "must not advance while pending")

    def test_bio_retirement_in_files_is_rejected(self):
        sources = self.changed(
            "vfs",
            "\tif (phase == VFS_SCOPE_RECLAIM_FILES) {\n"
            "\t\tint status = fs_reclaim_scope_files(scope_id);",
            "\tif (phase == VFS_SCOPE_RECLAIM_FILES) {\n"
            "\t\tbio_scope_retire(scope_id);\n"
            "\t\tint status = fs_reclaim_scope_files(scope_id);",
        )
        self.assert_rejected(sources, "FILES phase crosses phase ownership")

    def test_metadata_state_cannot_retire_before_settlement(self):
        guard = (
            "\tif (!agent_meta_store_failed_closed &&\n"
            "\t    !agent_file_writeback_scope_reached(scope_id, target, 1))\n"
            "\t\treturn 0;\n"
            "\tagent_file_scope_state_retire(scope_id);"
        )
        early = (
            "\tagent_file_scope_state_retire(scope_id);\n"
            "\tif (!agent_meta_store_failed_closed &&\n"
            "\t    !agent_file_writeback_scope_reached(scope_id, target, 1))\n"
            "\t\treturn 0;"
        )
        sources = self.changed("store", guard, early)
        self.assert_rejected(sources, "retired before its target settled")

    def test_missing_begin_cleanup_is_rejected(self):
        sources = self.changed(
            "objects", "\tagent_observe_scope_reclaim(scope_id);\n", ""
        )
        self.assert_rejected(sources, "lost observability cleanup ownership")

    def test_legacy_all_in_one_entry_is_rejected(self):
        sources = copy.deepcopy(self.good)
        sources["os_sources"]["os/legacy_reclaim.h"] = (
            "int agent_scope_reclaim(uint scope_id, int preserve_files);\n"
        )
        self.assert_rejected(sources, "legacy all-in-one reclaim entry")

    def test_vfs_cannot_bypass_metadata_owner(self):
        sources = copy.deepcopy(self.good)
        bridge = next(
            entry
            for entry in sources["budget"]["agent_modules"]["integration_bridges"]
            if entry["name"] == "vfs_security"
        )
        bridge["allowed_dependencies"].append("metadata_store")
        self.assert_rejected(sources, "dependency bypasses metadata_objects")


if __name__ == "__main__":
    unittest.main()
