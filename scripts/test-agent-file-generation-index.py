#!/usr/bin/env python3
"""固定生命周期 bank 与 inode 代际失效的变异测试。"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts/check-agent-file-generation-index.py"
SPEC = importlib.util.spec_from_file_location("file_generation_checker", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

FIXTURES = (
    "os/agent_file_state.c",
    "os/agent_file_state_internal.h",
    "os/agent_metadata_catalog.c",
    "os/vfs_security.c",
)


class IdentityInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in FIXTURES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def mutate_compact(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = CHECKER.compact(path)
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_repository_contract_passes(self) -> None:
        CHECKER.check(self.root)

    def test_rejects_lifecycle_blind_bank_hit(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "candidate->used&&candidate->scope_id==scope_id&&"
            "workflow_lifecycle_key_equal(candidate->lifecycle,lifecycle)",
            "candidate->used&&candidate->scope_id==scope_id",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_reusing_an_occupied_bank(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "!candidate->used&&free_state==0",
            "free_state==0",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_system_using_a_workflow_bank(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "state=&agent_file_cache_scopes[AGENT_FILE_CACHE_SYSTEM_SLOT]",
            "state=&agent_file_cache_scopes[1]",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_generation_baseline_reset_on_bank_reuse(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "state->cache_generation=scope_id==VFS_SCOPE_SYSTEM?"
            "agent_file_system_generation:agent_file_generation",
            "state->cache_generation=0",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_scope_generation_with_interrupt_only_serialization(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "intenabled=agent_edit_lock();"
            "state=agent_file_cache_scope_locked(scope_id,1);",
            "intenabled=intr_save();"
            "state=agent_file_cache_scope_locked(scope_id,1);",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_a_local_only_generation_guard(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "__sync_lock_test_and_set(&agent_file_edit_guard,1)",
            "agent_file_edit_guard",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_unlocked_public_generation_publication(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "intenabled=agent_edit_lock();generation="
            "agent_file_state_generation_next_capture_locked(scope_id,0);",
            "intenabled=0;generation="
            "agent_file_state_generation_next_capture_locked(scope_id,0);",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_nested_lock_in_locked_generation_helper(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "uint64generation;if(lifecycle)",
            "uint64generation;intenabled=agent_edit_lock();if(lifecycle)",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_recursive_generation_lock_during_content_publish(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "agent_file_state_generation_next_capture_locked("
            "ip->vfs_scope_id,&lifecycle)",
            "agent_file_state_generation_next(ip->vfs_scope_id)",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_entry_eviction_that_releases_the_bank(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "scope_state->version_count--;if(position",
            "scope_state->version_count--;"
            "memset(scope_state,0,sizeof(*scope_state));if(position",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_reclaim_that_retains_bank_ownership(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "capacity*sizeof(agent_file_versions[0]));"
            "memset(state,0,sizeof(*state));}agent_edit_unlock(enabled);",
            "capacity*sizeof(agent_file_versions[0]));}agent_edit_unlock(enabled);",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_unbind_without_cache_invalidation(self) -> None:
        self.mutate_compact(
            "os/agent_metadata_catalog.c",
            "agent_file_state_unbind_catalog_identity("
            "meta->dev,meta->inum,meta->incarnation,scope_id);",
            "",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_reused_content_generation(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "entry->content_version="
            "agent_file_counter_next(&agent_file_content_generation);",
            "entry->content_version=0;",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_partial_inode_identity(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "if(entry->incarnation!=incarnation)"
            "returnentry->incarnation<incarnation?-1:1;",
            "",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_catalog_unbind_that_revokes_edit_lease(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "entry->published_lifecycle=workflow_lifecycle_none();"
            "file_version_digest_clear_locked(entry);",
            "entry->published_lifecycle=workflow_lifecycle_none();"
            "file_version_clear_locked(i);",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_lookup_in_a_different_lifecycle_bank(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "state=file_version_scope_state_locked(scope_id,lifecycle,0);"
            "returnstate?file_version_search_locked(",
            "state=file_version_scope_state_locked("
            "scope_id,workflow_lifecycle_none(),0);"
            "returnstate?file_version_search_locked(",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)

    def test_rejects_sparse_insertion_into_a_dense_bank(self) -> None:
        self.mutate_compact(
            "os/agent_file_state.c",
            "memmove(&agent_file_versions[start+position+1],"
            "&agent_file_versions[start+position],"
            "(scope_state->version_count-position)*sizeof(*entry));",
            "",
        )
        with self.assertRaises(CHECKER.ContractError):
            CHECKER.check(self.root)


if __name__ == "__main__":
    unittest.main()
