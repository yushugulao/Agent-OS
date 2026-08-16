#!/usr/bin/env python3
"""Tests for the controlled Nexus development broker."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_dev as dev


class NexusDevelopmentBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        (self.root / "user" / "src").mkdir(parents=True)
        self.broker = dev.NexusDevelopmentBroker(
            self.root, temporary_parent=Path(self.temporary.name)
        )

    def tearDown(self) -> None:
        self.broker.close()
        self.temporary.cleanup()

    @staticmethod
    def revision(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_write_requires_allowed_path_and_exact_revision(self) -> None:
        denied = self.broker.write_file("README.md", "bad", "missing")
        self.assertEqual(denied.status, "ok")

        created = self.broker.write_file(
            "user/src/nexus_calc_ucore.c", "int main(void) { return 0; }\n", "missing"
        )
        self.assertEqual(created.status, "ok")
        revision = self.revision("int main(void) { return 0; }\n")
        self.assertEqual(created.workspace_generation, revision)
        self.assertIn("atomic_commit=1", created.content)

        conflict = self.broker.write_file(
            "user/src/nexus_calc_ucore.c", "changed\n", "missing"
        )
        self.assertEqual(conflict.status, "ok")
        self.assertIn("code=revision_conflict", conflict.content)
        self.assertEqual(
            (self.root / "user/src/nexus_calc_ucore.c").read_text(encoding="utf-8"),
            "int main(void) { return 0; }\n",
        )

    def test_apply_patch_is_revision_checked_and_atomic(self) -> None:
        path = self.root / "user/src/nexus_calc_ucore.c"
        path.write_text("one\ntwo\n", encoding="utf-8", newline="\n")
        before = self.revision("one\ntwo\n")
        patch = (
            "--- a/user/src/nexus_calc_ucore.c\n"
            "+++ b/user/src/nexus_calc_ucore.c\n"
            "@@ -1,2 +1,2 @@\n"
            " one\n"
            "-two\n"
            "+three\n"
        )
        result = self.broker.apply_patch(
            "user/src/nexus_calc_ucore.c", patch, before
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(path.read_text(encoding="utf-8"), "one\nthree\n")

        rejected = self.broker.apply_patch(
            "user/src/nexus_calc_ucore.c", patch, before
        )
        self.assertEqual(rejected.status, "ok")
        self.assertIn("revision_conflict", rejected.content)
        self.assertEqual(path.read_text(encoding="utf-8"), "one\nthree\n")

    def test_chunked_write_is_invisible_until_revision_checked_commit(self) -> None:
        relative = "user/src/nexus_chunked_ucore.c"
        target = self.root / relative
        first = self.broker.write_file_chunk(
            relative, "first\n", "missing", "", 0
        )
        self.assertFalse(target.exists())
        write_id = next(
            line.split("=", 1)[1]
            for line in first.content.splitlines()
            if line.startswith("write_id=")
        )
        second = self.broker.write_file_chunk(
            relative, "second\n", "missing", write_id, 0
        )
        self.assertIn("staged_bytes=13", second.content)
        self.assertFalse(target.exists())
        committed = self.broker.write_file_chunk(
            relative, "third\n", "missing", write_id, 1
        )
        self.assertIn("atomic_commit=1", committed.content)
        self.assertEqual(target.read_text(encoding="utf-8"), "first\nsecond\nthird\n")

        stale = self.broker.write_file_chunk(relative, "x", "missing", "", 0)
        self.assertIn("revision_conflict", stale.content)
        self.assertEqual(target.read_text(encoding="utf-8"), "first\nsecond\nthird\n")

    def test_codex_patch_dialect_is_revision_checked_and_atomic(self) -> None:
        relative = "user/src/nexus_codex_ucore.c"
        target = self.root / relative
        target.write_text("one\ntwo\n", encoding="utf-8", newline="\n")
        before = self.revision("one\ntwo\n")
        result = self.broker.apply_patch(
            relative,
            "*** Begin Patch\n"
            f"*** Update File: {relative}\n"
            "@@\n"
            " one\n"
            "-two\n"
            "+three\n"
            "*** End Patch\n",
            before,
        )
        self.assertIn("atomic_commit=1", result.content)
        self.assertEqual(target.read_text(encoding="utf-8"), "one\nthree\n")

    def test_symlink_target_is_rejected(self) -> None:
        outside = Path(self.temporary.name) / "outside.c"
        outside.write_text("outside\n", encoding="utf-8")
        target = self.root / "user/src/nexus_link_ucore.c"
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        result = self.broker.write_file(
            "user/src/nexus_link_ucore.c", "inside\n", "missing"
        )
        self.assertEqual(result.status, "ok")
        self.assertIn("path_symlink", result.content)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_build_rejects_unknown_target_before_subprocess(self) -> None:
        source = self.root / "user/src/nexus_calc_ucore.c"
        source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        with mock.patch.object(self.broker, "_copy_worktree") as copied:
            result = self.broker.build_ucore_program(
                "user/src/nexus_calc_ucore.c", "nexus_other_ucore"
            )
        self.assertEqual(result.status, "ok")
        self.assertIn("target_source_mismatch", result.content)
        copied.assert_not_called()

    def test_run_requires_owned_build_and_case_kind(self) -> None:
        unknown = self.broker.run_ucore_program(
            "0" * 64, "1 + 1\n", "2", 0, "normal"
        )
        self.assertEqual(unknown.status, "ok")
        self.assertIn("build_id_unknown", unknown.content)
        invalid = self.broker.run_ucore_program(
            "0" * 64, "", "", 0, "other"
        )
        self.assertEqual(invalid.status, "ok")


if __name__ == "__main__":
    unittest.main()
