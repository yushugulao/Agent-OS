#!/usr/bin/env python3
"""Model and mutation tests for the filesystem epoch block index."""

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-fs-epoch-index.py"
SPEC = importlib.util.spec_from_file_location("check_fs_epoch_index", CHECKER)
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
SOURCE = (ROOT / "os/fs_epoch.c").read_text(encoding="utf-8")
HEADER = (ROOT / "os/fs_epoch.h").read_text(encoding="utf-8")


class EpochIndexModel:
    CAPACITY = 64

    def __init__(self):
        self.next_generation = 0
        self.active_generation = 0
        self.slots = [(0, 0)] * self.CAPACITY
        self.entries = []
        self.phase_count = [0] * 4
        self.deduplicated = 0
        self.max_probes = 0

    @staticmethod
    def hash(dev, blockno):
        key = (blockno ^ (dev * 0x9E3779B9)) & 0xFFFFFFFF
        key ^= key >> 16
        key = (key * 0x7FEB352D) & 0xFFFFFFFF
        key ^= key >> 15
        return key & 63

    def start(self):
        self.next_generation += 1
        self.active_generation = self.next_generation

    def lookup(self, dev, blockno):
        if self.active_generation == 0:
            return None, None
        bucket = self.hash(dev, blockno)
        for probe in range(1, self.CAPACITY + 1):
            slot_index = (bucket + probe - 1) & 63
            generation, entry_plus_one = self.slots[slot_index]
            self.max_probes = max(self.max_probes, probe)
            if generation != self.active_generation:
                return None, slot_index
            entry = self.entries[entry_plus_one - 1]
            if entry[0:2] == (dev, blockno):
                return entry_plus_one - 1, slot_index
        raise AssertionError("index saturated")

    def stage(self, dev, blockno, phase):
        entry_index, slot_index = self.lookup(dev, blockno)
        if entry_index is not None:
            old_phase = self.entries[entry_index][2]
            if {old_phase, phase} == {1, 3}:
                raise ValueError("incompatible namespace phases")
            if phase > old_phase:
                self.phase_count[old_phase] -= 1
                self.entries[entry_index] = (dev, blockno, phase)
                self.phase_count[phase] += 1
            self.deduplicated += 1
            return
        if len(self.entries) == 48:
            raise OverflowError("epoch full")
        self.entries.append((dev, blockno, phase))
        self.slots[slot_index] = (self.active_generation, len(self.entries))
        self.phase_count[phase] += 1

    def dirty(self, dev, blockno):
        entry_index, _ = self.lookup(dev, blockno)
        return entry_index is not None

    def commit(self):
        self.entries.clear()
        self.phase_count = [0] * 4
        self.active_generation = 0


class EpochIndexBehaviorTests(unittest.TestCase):
    def test_full_overwrite_batch_stays_indexed(self):
        model = EpochIndexModel()
        model.start()
        for blockno in range(100, 136):
            model.stage(1, blockno, 0)
        for blockno in range(100, 136):
            model.stage(1, blockno, 2)
        self.assertEqual(len(model.entries), 36)
        self.assertEqual(model.phase_count, [0, 0, 36, 0])
        self.assertEqual(model.deduplicated, 36)
        self.assertTrue(all(model.dirty(1, blockno) for blockno in range(100, 136)))
        self.assertLessEqual(model.max_probes, model.CAPACITY)

    def test_collisions_probe_without_losing_keys(self):
        by_bucket = {}
        collision = None
        for blockno in range(4096):
            values = by_bucket.setdefault(EpochIndexModel.hash(7, blockno), [])
            values.append(blockno)
            if len(values) == 4:
                collision = values
                break
        self.assertIsNotNone(collision)
        model = EpochIndexModel()
        model.start()
        for blockno in collision:
            model.stage(7, blockno, 0)
        self.assertTrue(all(model.dirty(7, blockno) for blockno in collision))
        self.assertGreaterEqual(model.max_probes, 4)

    def test_repeat_stage_preserves_phase_rules(self):
        model = EpochIndexModel()
        model.start()
        model.stage(2, 9, 0)
        model.stage(2, 9, 2)
        model.stage(2, 9, 0)
        self.assertEqual(model.entries[0][2], 2)
        self.assertEqual(model.deduplicated, 2)
        model.stage(2, 10, 1)
        with self.assertRaises(ValueError):
            model.stage(2, 10, 3)

    def test_commit_invalidates_old_generation(self):
        model = EpochIndexModel()
        model.start()
        model.stage(3, 44, 0)
        old_generation = model.active_generation
        self.assertTrue(model.dirty(3, 44))
        model.commit()
        self.assertFalse(model.dirty(3, 44))
        model.start()
        self.assertNotEqual(model.active_generation, old_generation)
        self.assertFalse(model.dirty(3, 44))
        model.stage(3, 44, 0)
        self.assertTrue(model.dirty(3, 44))


class EpochIndexMutationTests(unittest.TestCase):
    def mutate(self, source=SOURCE, header=HEADER, *, old, new):
        target = source if old in source else header
        self.assertEqual(target.count(old), 1, f"mutation anchor drift: {old}")
        changed = target.replace(old, new, 1)
        if target is source:
            source = changed
        else:
            header = changed
        with self.assertRaises(checker.ContractError):
            checker.check_text(source, header)

    def test_current_tree_passes(self):
        checker.check_text(SOURCE, HEADER)

    def test_stale_generation_guard_is_required(self):
        self.mutate(old="epoch.index_generation[slot_index] !=\n\t\t    epoch.active_generation",
                    new="epoch.index_generation[slot_index] == 0")

    def test_lookup_bound_is_required(self):
        self.mutate(old="probe <= FS_EPOCH_INDEX_CAP",
                    new="probe != 0")

    def test_collision_probe_step_is_required(self):
        self.mutate(old="bucket + probe - 1",
                    new="bucket")

    def test_probe_metric_is_required(self):
        self.mutate(old="epoch.totals.max_lookup_probes < probe",
                    new="0")

    def test_stage_must_use_index(self):
        self.mutate(old="fs_epoch_index_lookup_locked(bp->dev, bp->blockno,",
                    new="fs_epoch_missing_lookup(bp->dev, bp->blockno,")

    def test_stage_must_publish_new_entry(self):
        self.mutate(old="fs_epoch_index_publish_locked(publication_slot, entry_index);",
                    new="/* index publication omitted */")

    def test_dirty_query_must_use_index(self):
        self.mutate(old="fs_epoch_index_lookup_locked(dev, blockno, 0, 0);",
                    new="0;")

    def test_commit_must_invalidate_generation(self):
        self.mutate(old="\tepoch.active_generation = 0;",
                    new="\t/* active generation retained */")

    def test_publication_marker_must_be_last(self):
        old = ("\tepoch.index_entry_plus_one[slot_index] = entry_index + 1;\n"
               "\tepoch.index_generation[slot_index] = epoch.active_generation;")
        new = ("\tepoch.index_generation[slot_index] = epoch.active_generation;\n"
               "\tepoch.index_entry_plus_one[slot_index] = entry_index + 1;")
        self.mutate(old=old, new=new)

    def test_stats_abi_must_expose_probe_bound(self):
        self.mutate(old="uint max_lookup_probes;",
                    new="uint reserved_lookup_metric;")


if __name__ == "__main__":
    unittest.main()
