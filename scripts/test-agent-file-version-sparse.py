#!/usr/bin/env python3
"""Mutation and behavior tests for the fixed-bank file-version contract."""

from __future__ import annotations

import bisect
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts/check-agent-file-version-sparse.py"
SPEC = importlib.util.spec_from_file_location("file_version_checker", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

FIXTURES = (
    "os/agent_file_state.c",
    "os/agent.h",
    "os/workflow_lifecycle.h",
)


class DenseBank:
    """Small executable model of the C bank insertion and eviction rules."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.keys: list[int] = []
        self.published: set[int] = set()
        self.editing: set[int] = set()
        self.cursor = 0

    def _delete(self, position: int) -> None:
        key = self.keys.pop(position)
        self.published.discard(key)
        self.editing.discard(key)
        if self.cursor > position:
            self.cursor -= 1
        if not self.keys or self.cursor >= len(self.keys):
            self.cursor = 0

    def insert(self, key: int) -> bool:
        position = bisect.bisect_left(self.keys, key)
        if position < len(self.keys) and self.keys[position] == key:
            return False
        if len(self.keys) >= self.capacity:
            count = len(self.keys)
            victim = None
            for walked in range(count):
                candidate = (self.cursor + walked) % count
                candidate_key = self.keys[candidate]
                if (
                    candidate_key not in self.published
                    and candidate_key not in self.editing
                ):
                    victim = candidate
                    break
            if victim is None:
                return False
            self.cursor = (victim + 1) % count
            self._delete(victim)
            position = bisect.bisect_left(self.keys, key)
        if self.keys and position <= self.cursor:
            self.cursor += 1
        self.keys.insert(position, key)
        if self.cursor >= len(self.keys):
            self.cursor = 0
        return True


class EditVersionModel:
    """Per-file clocks plus a high-water seed used only after cold rebuild."""

    def __init__(self) -> None:
        self.high_water = 0
        self.versions: dict[str, int] = {}

    def open(self, identity: str) -> int:
        return self.versions.setdefault(identity, self.high_water)

    def dirty_commit(self, identity: str) -> tuple[int, int]:
        base = self.open(identity)
        current = base + 1
        self.versions[identity] = current
        self.high_water = max(self.high_water, current)
        return base, current

    def evict(self, identity: str) -> None:
        del self.versions[identity]


class FixedBankBehaviorTests(unittest.TestCase):
    def test_120_unique_begins_reuse_only_cold_workflow_entries(self) -> None:
        bank = DenseBank(112)
        for identity in range(120):
            self.assertTrue(bank.insert(identity))
        self.assertEqual(bank.keys, list(range(8, 120)))
        self.assertEqual(len(bank.keys), 112)

    def test_published_and_editing_entries_are_not_evictable(self) -> None:
        bank = DenseBank(4)
        for identity in range(4):
            self.assertTrue(bank.insert(identity))
        bank.published.update((0, 2))
        bank.editing.update((1, 3))
        self.assertFalse(bank.insert(4))
        self.assertEqual(bank.keys, [0, 1, 2, 3])

    def test_unrelated_commits_do_not_jump_a_live_file_version(self) -> None:
        versions = EditVersionModel()
        self.assertEqual(versions.dirty_commit("a"), (0, 1))
        self.assertEqual(versions.open("b"), 1)
        self.assertEqual(versions.dirty_commit("b"), (1, 2))
        self.assertEqual(versions.dirty_commit("b"), (2, 3))
        self.assertEqual(versions.open("a"), 1)
        self.assertEqual(versions.dirty_commit("a"), (1, 2))
        self.assertEqual(versions.high_water, 3)

        versions.evict("a")
        self.assertEqual(versions.open("a"), 3)
        self.assertEqual(versions.dirty_commit("a"), (3, 4))


class FixedBankMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in FIXTURES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def mutate_source(self, old: str, new: str) -> None:
        path = self.root / "os/agent_file_state.c"
        source = CHECKER.compact(path.read_text(encoding="utf-8"))
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CHECKER.check(self.root)

    def test_repository_contract_passes(self) -> None:
        CHECKER.check(self.root)

    def test_rejects_global_clock_as_a_live_file_version(self) -> None:
        self.mutate_source(
            "next=agent_file_counter_next(&entry->edit_version);",
            "next=agent_file_counter_next(&agent_file_edit_version_generation);"
            "entry->edit_version=next;",
        )
        self.assert_rejected()

    def test_rejects_high_water_assignment_during_commit(self) -> None:
        self.mutate_source(
            "if(call.entry->dirty)"
            "(void)file_version_edit_next_locked(version);",
            "if(call.entry->dirty)"
            "version->edit_version=agent_file_edit_version_generation;",
        )
        self.assert_rejected()

    def test_rejects_lifecycle_blind_bank_ownership(self) -> None:
        self.mutate_source(
            "candidate->used&&candidate->scope_id==scope_id&&"
            "workflow_lifecycle_key_equal(candidate->lifecycle,lifecycle)",
            "candidate->used&&candidate->scope_id==scope_id",
        )
        self.assert_rejected()

    def test_rejects_evicting_pending_publication(self) -> None:
        self.mutate_source(
            "if(entry->published_size_valid||"
            "file_version_has_edit_locked(entry))",
            "if(file_version_has_edit_locked(entry))",
        )
        self.assert_rejected()

    def test_rejects_evicting_an_active_edit(self) -> None:
        self.mutate_source(
            "if(entry->published_size_valid||"
            "file_version_has_edit_locked(entry))",
            "if(entry->published_size_valid)",
        )
        self.assert_rejected()

    def test_rejects_partial_inode_identity(self) -> None:
        self.mutate_source(
            "if(entry->incarnation!=incarnation)"
            "returnentry->incarnation<incarnation?-1:1;",
            "",
        )
        self.assert_rejected()

    def test_rejects_sparse_insertion(self) -> None:
        self.mutate_source(
            "memmove(&agent_file_versions[start+position+1],"
            "&agent_file_versions[start+position],"
            "(scope_state->version_count-position)*sizeof(*entry));",
            "",
        )
        self.assert_rejected()

    def test_rejects_releasing_bank_ownership_without_scope_reclaim(self) -> None:
        self.mutate_source(
            "scope_state->version_count--;if(position",
            "scope_state->version_count--;"
            "memset(scope_state,0,sizeof(*scope_state));if(position",
        )
        self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
