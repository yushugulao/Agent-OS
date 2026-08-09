#!/usr/bin/env python3
"""Mutation tests for the member+closing teardown contract."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-teardown-protocol.py"
SPEC = importlib.util.spec_from_file_location("teardown_protocol", CHECKER)
assert SPEC is not None and SPEC.loader is not None
teardown_protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teardown_protocol)


class TeardownProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.good = teardown_protocol.load_sources(ROOT)

    def changed(self, key: str, old: str, new: str) -> dict[str, str]:
        sources = copy.deepcopy(self.good)
        self.assertGreaterEqual(sources[key].count(old), 1, f"mutation anchor drift: {old}")
        sources[key] = sources[key].replace(old, new, 1)
        return sources

    def changed_function(
        self, key: str, name: str, old: str, new: str
    ) -> dict[str, str]:
        sources = copy.deepcopy(self.good)
        body = teardown_protocol.function(sources[key], name)
        self.assertEqual(body.count(old), 1, f"mutation anchor drift in {name}: {old}")
        sources[key] = sources[key].replace(body, body.replace(old, new, 1), 1)
        return sources

    def changed_function_all(
        self, key: str, name: str, old: str, new: str
    ) -> dict[str, str]:
        sources = copy.deepcopy(self.good)
        body = teardown_protocol.function(sources[key], name)
        self.assertGreater(body.count(old), 0, f"mutation anchor drift in {name}: {old}")
        sources[key] = sources[key].replace(body, body.replace(old, new), 1)
        return sources

    def rejected(self, sources: dict[str, str]) -> None:
        with self.assertRaises(teardown_protocol.ProtocolError):
            teardown_protocol.validate_protocol(sources)

    def test_repository_protocol_is_valid(self) -> None:
        teardown_protocol.validate_protocol(copy.deepcopy(self.good))

    def test_lifecycle_mutations_are_rejected(self) -> None:
        cases = (
            ("uintmembers;", "uintmembers_removed;"),
            ("record->members=1", "record->members=0"),
            ("!record->closing&&!record->fence_gate", "!record->fence_gate"),
            ("record->members--", "record->members-=0"),
            ("record->closing=1", "record->closing=0"),
            ("if(record->fence_gate)", "if(0)"),
            ("record->active_operations++", "record->active_operations+=0"),
            ("record->departing_operations++", "record->departing_operations+=0"),
            ("record->fence_gate=1", "record->fence_gate=0"),
            ("fence_sequence==record->fence_sequence+1", "fence_sequence!=0"),
            ("record->used=0", "record->used=1"),
        )
        for old, new in cases:
            with self.subTest(anchor=old):
                self.rejected(self.changed("lifecycle", old, new))

        self.rejected(
            self.changed_function(
                "lifecycle",
                "workflow_lifecycle_fence_begin",
                "record->departing_operations==0",
                "record->departing_operations>=0",
            )
        )

    def test_departure_must_remain_legal_after_close(self) -> None:
        anchor = "if(record!=0&&record->members>0&&!record->fence_gate&&"
        mutated = anchor.replace("record->members>0", "!record->closing&&record->members>0")
        self.rejected(self.changed("lifecycle", anchor, mutated))

    def test_staged_vfs_retirement_is_rejected(self) -> None:
        sources = copy.deepcopy(self.good)
        sources["vfs"] += "enum VFS_SCOPE_RECLAIM_OLD { VFS_SCOPE_RECLAIM_BEGIN };"
        self.rejected(sources)

    def test_vfs_mutations_are_rejected(self) -> None:
        cases = (
            ("vfs_scope_release", "workflow_lifecycle_departure_leave(lifecycle);", ""),
            ("vfs_scope_release", "fs_storage_scope_account_close(storage);", ""),
            ("vfs_scope_release", "bio_scope_quiesce(scope_id);", ""),
            ("vfs_scope_reclaim_prepare", "agent_scope_reclaim_begin(scope_id,lifecycle,&ignored_target)", "0"),
            ("vfs_scope_reclaim_prepare", "ref->cleanup_started=1", "ref->cleanup_started=0"),
            ("vfs_scope_reap_pending", "files_status=fs_reclaim_scope_files(scope_id)", "files_status=0"),
            ("vfs_scope_reclaim_finish", "bio_scope_retire(scope_id);", ""),
            ("vfs_scope_reclaim_finish", "workflow_lifecycle_reclaim(lifecycle)", "0"),
            ("vfs_scope_reap_pending", "if(request_next)agent_background_request();", ""),
        )
        for name, old, new in cases:
            with self.subTest(function=name, anchor=old):
                mutate = (
                    self.changed_function_all
                    if name == "vfs_scope_reclaim_finish"
                    and old == "workflow_lifecycle_reclaim(lifecycle)"
                    else self.changed_function
                )
                self.rejected(mutate("vfs", name, old, new))

    def test_process_mutations_are_rejected(self) -> None:
        cases = (
            ("proc_teardown_claim_locked", "workflow_lifecycle_departure_enter(lifecycle)<0", "0"),
            ("proc_teardown_run", "files[i]=p->files[i];p->files[i]=0", "files[i]=p->files[i]"),
            ("proc_teardown_run", "fileclose_batch_add(&close_batch,files[i])", "0"),
            ("proc_teardown_run", "freepagetable_cleanup(p->pagetable,p->max_page)", "0"),
            ("proc_teardown_run", "vfs_proc_lifecycle_release(p);", ""),
            ("scheduler_finish_dying_thread", "workflow_lifecycle_departure_leave(lifecycle);", ""),
        )
        for name, old, new in cases:
            with self.subTest(function=name, anchor=old):
                self.rejected(self.changed_function("proc", name, old, new))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TeardownProtocolTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if result.wasSuccessful():
        print("teardown protocol mutation tests passed")
    raise SystemExit(0 if result.wasSuccessful() else 1)
