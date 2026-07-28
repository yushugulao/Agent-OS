#!/usr/bin/env python3

import copy
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).with_name("fs-allocator-image.py")
SPEC = importlib.util.spec_from_file_location("fs_allocator_image", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class AllocatorImageTest(unittest.TestCase):
    def make_image(self, path: Path) -> None:
        size = 64
        ninodes = 16
        inodestart = 2
        bmapstart = 4
        qmapstart = 5
        datastart = 6
        image = bytearray(size * MODULE.BLOCK_SIZE)
        policy = (2, 4, 2, 1, 1, 1, 1)
        policy_checksum = MODULE.storage_policy_checksum(policy)
        superblock = (
            MODULE.AGENT_FS_MAGIC,
            size,
            size - datastart,
            ninodes,
            inodestart,
            bmapstart,
            qmapstart,
            datastart,
            2,
            4,
            1,
            1,
            1,
            1,
            2,
            policy_checksum,
        )
        struct.pack_into("<16I", image, MODULE.BLOCK_SIZE, *superblock)
        self.write_inode(image, 1, inode_type=1, size=256, addrs=[6], owner=1)
        self.write_inode(image, 2, inode_type=2, size=1024, addrs=[7], owner=2)
        self.write_inode(image, 3, inode_type=2, size=1, addrs=[8], owner=2)
        self.write_dirent(image, 0, 2, b"fixture")
        self.write_dirent(image, 1, 3, b"fsalloc_state")
        image[8 * MODULE.BLOCK_SIZE] = ord("P")
        for block, owner in ((6, 1), (7, 2), (8, 2)):
            image[bmapstart * MODULE.BLOCK_SIZE + block // 8] |= 1 << (block % 8)
            struct.pack_into(
                "<I", image, qmapstart * MODULE.BLOCK_SIZE + block * 4, owner
            )
        path.write_bytes(image)

    @staticmethod
    def write_inode(image, inum, *, inode_type, size, addrs, owner):
        offset = 2 * MODULE.BLOCK_SIZE + inum * MODULE.DINODE_SIZE
        image[offset : offset + MODULE.DINODE_SIZE] = b"\0" * MODULE.DINODE_SIZE
        struct.pack_into("<h", image, offset, inode_type)
        struct.pack_into("<I", image, offset + 8, size)
        padded = list(addrs) + [0] * (13 - len(addrs))
        struct.pack_into("<13I", image, offset + 12, *padded)
        if inode_type:
            flags = (
                MODULE.VFS_LABEL_F_ROOT
                if inum == MODULE.ROOT_INODE
                else MODULE.VFS_LABEL_F_PUBLIC
            )
            policy = (
                MODULE.VFS_POLICY_ROOT
                if inum == MODULE.ROOT_INODE
                else MODULE.VFS_POLICY_PUBLIC
            )
            struct.pack_into("<I", image, offset + 84, MODULE.VFS_LABEL_MAGIC)
            struct.pack_into("<I", image, offset + 88, MODULE.VFS_LABEL_VERSION)
            struct.pack_into("<I", image, offset + 92, flags)
            struct.pack_into("<I", image, offset + 100, policy)
            struct.pack_into("<I", image, offset + 108, MODULE.VFS_POLICY_GENERATION)
            struct.pack_into("<I", image, offset + 112, 1)
        struct.pack_into("<I", image, offset + 116, owner)
        struct.pack_into("<I", image, offset + 120, 3 if owner else 0)
        if inode_type:
            checksum = MODULE.vfs_label_checksum(
                inum,
                (
                    MODULE.VFS_LABEL_MAGIC,
                    MODULE.VFS_LABEL_VERSION,
                    flags,
                    0,
                    policy,
                    0,
                    MODULE.VFS_POLICY_GENERATION,
                    1,
                    owner,
                    MODULE.FS_OWNER_VERSION,
                ),
            )
            struct.pack_into("<I", image, offset + 124, checksum)

    @staticmethod
    def write_dirent(image, index, inum, name):
        struct.pack_into(
            "<H14s",  image, 6 * MODULE.BLOCK_SIZE + index * 16, inum, name
        )

    @staticmethod
    def write_free_transition_inode(image, inum, owner):
        offset = 2 * MODULE.BLOCK_SIZE + inum * MODULE.DINODE_SIZE
        image[offset : offset + MODULE.DINODE_SIZE] = b"\0" * MODULE.DINODE_SIZE
        struct.pack_into("<I", image, offset + 84, MODULE.VFS_LABEL_MAGIC)
        struct.pack_into("<I", image, offset + 88, MODULE.VFS_LABEL_VERSION)
        struct.pack_into("<I", image, offset + 92, MODULE.VFS_LABEL_F_FREE)
        struct.pack_into("<I", image, offset + 100, MODULE.VFS_POLICY_FREE)
        struct.pack_into("<I", image, offset + 108, MODULE.VFS_POLICY_GENERATION)
        struct.pack_into("<I", image, offset + 112, 1)
        struct.pack_into("<I", image, offset + 116, owner)
        struct.pack_into("<I", image, offset + 120, MODULE.FS_OWNER_VERSION)
        checksum = MODULE.vfs_label_checksum(
            inum,
            (
                MODULE.VFS_LABEL_MAGIC,
                MODULE.VFS_LABEL_VERSION,
                MODULE.VFS_LABEL_F_FREE,
                0,
                MODULE.VFS_POLICY_FREE,
                0,
                MODULE.VFS_POLICY_GENERATION,
                1,
                owner,
                MODULE.FS_OWNER_VERSION,
            ),
        )
        struct.pack_into("<I", image, offset + 124, checksum)

    @staticmethod
    def write_free_inode(image, inum, incarnation=1):
        offset = 2 * MODULE.BLOCK_SIZE + inum * MODULE.DINODE_SIZE
        image[offset : offset + MODULE.DINODE_SIZE] = b"\0" * MODULE.DINODE_SIZE
        struct.pack_into("<I", image, offset + 84, MODULE.VFS_LABEL_MAGIC)
        struct.pack_into("<I", image, offset + 88, MODULE.VFS_LABEL_VERSION)
        struct.pack_into("<I", image, offset + 92, MODULE.VFS_LABEL_F_FREE)
        struct.pack_into("<I", image, offset + 100, MODULE.VFS_POLICY_FREE)
        struct.pack_into("<I", image, offset + 108, MODULE.VFS_POLICY_GENERATION)
        struct.pack_into("<I", image, offset + 112, incarnation)
        checksum = MODULE.vfs_label_checksum(
            inum,
            (
                MODULE.VFS_LABEL_MAGIC,
                MODULE.VFS_LABEL_VERSION,
                MODULE.VFS_LABEL_F_FREE,
                0,
                MODULE.VFS_POLICY_FREE,
                0,
                MODULE.VFS_POLICY_GENERATION,
                incarnation,
                0,
                0,
            ),
        )
        struct.pack_into("<I", image, offset + 124, checksum)

    @staticmethod
    def set_stage(image, stage):
        image[8 * MODULE.BLOCK_SIZE] = ord(stage)

    @staticmethod
    def recompute_inode_checksum(image, inum):
        offset = 2 * MODULE.BLOCK_SIZE + inum * MODULE.DINODE_SIZE
        values = tuple(struct.unpack_from("<10I", image, offset + 84))
        checksum = MODULE.vfs_label_checksum(inum, values)
        struct.pack_into("<I", image, offset + 124, checksum)

    def make_ialloc_triplet(self, directory):
        before_path = directory / "before.img"
        fault_path = directory / "fault.img"
        reboot_path = directory / "reboot.img"
        self.make_image(before_path)
        fault = bytearray(before_path.read_bytes())
        self.set_stage(fault, "F")
        fault_path.write_bytes(fault)
        reboot_path.write_bytes(fault)
        return before_path, fault_path, reboot_path

    def make_ialloc_owner_busy_triplet(self, directory):
        paths = self.make_ialloc_triplet(directory)
        for path in paths[1:]:
            raw = bytearray(path.read_bytes())
            self.write_free_inode(raw, 4, 1)
            path.write_bytes(raw)
        return paths

    def make_free_crash_triplet(self, directory):
        before_path = directory / "before.img"
        fault_path = directory / "fault.img"
        reboot_path = directory / "reboot.img"
        self.make_image(before_path)
        before = bytearray(before_path.read_bytes())
        self.write_inode(before, 1, inode_type=1, size=256, addrs=[6], owner=1)
        self.write_inode(before, 4, inode_type=2, size=1024, addrs=[9], owner=2)
        self.write_dirent(before, 2, 4, b"fsalloc_free")
        before[9 * MODULE.BLOCK_SIZE : 10 * MODULE.BLOCK_SIZE] = bytes(
            (index ^ 0x5A) & 0xFF for index in range(MODULE.BLOCK_SIZE)
        )
        before[4 * MODULE.BLOCK_SIZE + 9 // 8] |= 1 << (9 % 8)
        struct.pack_into("<I", before, 5 * MODULE.BLOCK_SIZE + 9 * 4, 2)
        before_path.write_bytes(before)

        fault = bytearray(before)
        self.set_stage(fault, "F")
        self.write_dirent(fault, 2, 0, b"")
        self.write_free_inode(fault, 4)
        struct.pack_into(
            "<I",
            fault,
            5 * MODULE.BLOCK_SIZE + 9 * 4,
            (MODULE.OWNER_STATE_FREEING << 30) | 2,
        )
        fault_path.write_bytes(fault)

        reboot = bytearray(before)
        self.set_stage(reboot, "F")
        self.write_dirent(reboot, 2, 0, b"")
        self.write_free_inode(reboot, 4)
        reboot[4 * MODULE.BLOCK_SIZE + 9 // 8] &= ~(1 << (9 % 8))
        struct.pack_into("<I", reboot, 5 * MODULE.BLOCK_SIZE + 9 * 4, 0)
        reboot_path.write_bytes(reboot)
        return before_path, fault_path, reboot_path

    def make_alloc_triplet(self, directory, checkpoint):
        before_path = directory / "before.img"
        fault_path = directory / "fault.img"
        reboot_path = directory / "reboot.img"
        self.make_image(before_path)

        fault = bytearray(before_path.read_bytes())
        self.set_stage(fault, "F")
        self.write_inode(fault, 4, inode_type=2, size=0, addrs=[], owner=2)
        self.write_dirent(fault, 2, 4, b"fsalloc_block")
        if checkpoint == "allocating":
            struct.pack_into(
                "<I",
                fault,
                5 * MODULE.BLOCK_SIZE + 9 * 4,
                (MODULE.OWNER_STATE_ALLOCATING << 30) | 2,
            )
        elif checkpoint == "orphan":
            fault[4 * MODULE.BLOCK_SIZE + 9 // 8] |= 1 << (9 % 8)
            struct.pack_into("<I", fault, 5 * MODULE.BLOCK_SIZE + 9 * 4, 2)
        elif checkpoint != "none":
            raise AssertionError(checkpoint)
        fault_path.write_bytes(fault)

        reboot = bytearray(before_path.read_bytes())
        self.set_stage(reboot, "F")
        self.write_inode(reboot, 4, inode_type=2, size=0, addrs=[], owner=2)
        self.write_dirent(reboot, 2, 4, b"fsalloc_block")
        reboot_path.write_bytes(reboot)
        return before_path, fault_path, reboot_path

    def make_ialloc_crash_triplet(self, directory, phase):
        before_path = directory / "before.img"
        fault_path = directory / "fault.img"
        reboot_path = directory / "reboot.img"
        self.make_image(before_path)
        fault = bytearray(before_path.read_bytes())
        self.set_stage(fault, "F")
        if phase == "intent":
            self.write_free_transition_inode(
                fault, 4, (MODULE.OWNER_STATE_ALLOCATING << 30) | 2
            )
            struct.pack_into(
                "<h",
                fault,
                2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE,
                2,
            )
        elif phase == "owner":
            self.write_inode(fault, 4, inode_type=2, size=0, addrs=[], owner=2)
        else:
            raise AssertionError(phase)
        fault_path.write_bytes(fault)
        reboot = bytearray(before_path.read_bytes())
        self.set_stage(reboot, "F")
        self.write_free_inode(reboot, 4, 1)
        reboot_path.write_bytes(reboot)
        return before_path, fault_path, reboot_path

    def make_ifree_triplet(self, directory, phase, action):
        before_path = directory / "before.img"
        fault_path = directory / "fault.img"
        reboot_path = directory / "reboot.img"
        self.make_image(before_path)
        before = bytearray(before_path.read_bytes())
        self.write_inode(before, 4, inode_type=2, size=0, addrs=[], owner=2)
        self.write_dirent(before, 2, 4, b"fsalloc_ifree")
        before_path.write_bytes(before)

        fault = bytearray(before)
        self.set_stage(fault, "F")
        self.write_dirent(fault, 2, 0, b"")
        freeing = (MODULE.OWNER_STATE_FREEING << 30) | 2
        if action == "busy" or (action == "crash" and phase == "refund"):
            self.write_free_inode(fault, 4, 1)
        elif action == "eio" and phase == "intent":
            pass
        elif (action == "crash" and phase == "intent") or (
            action == "eio" and phase == "owner"
        ):
            inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
            struct.pack_into("<I", fault, inode_offset + 116, freeing)
            self.recompute_inode_checksum(fault, 4)
        elif (action == "crash" and phase == "owner") or (
            action == "eio" and phase == "refund"
        ):
            self.write_free_transition_inode(fault, 4, freeing)
        else:
            raise AssertionError((phase, action))
        fault_path.write_bytes(fault)

        reboot = bytearray(before)
        self.set_stage(reboot, "F")
        self.write_dirent(reboot, 2, 0, b"")
        self.write_free_inode(reboot, 4, 1)
        reboot_path.write_bytes(reboot)
        return before_path, fault_path, reboot_path

    def test_snapshot_exposes_raw_maps_and_reachability(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            state = MODULE.read_snapshot(image)
            image_bytes = image.read_bytes()
            semantic = {
                key: value
                for key, value in state.items()
                if key not in {"image", "generator", "state_sha256"}
            }
            semantic_sha256 = hashlib.sha256(
                json.dumps(
                    semantic, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        self.assertEqual(state["format"], MODULE.SNAPSHOT_FORMAT)
        self.assertEqual(state["generator"], MODULE.GENERATOR)
        self.assertEqual(
            state["image"],
            {
                "bytes": len(image_bytes),
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
            },
        )
        self.assertEqual(state["state_sha256"], semantic_sha256)
        self.assertEqual(state["allocated_blocks"], [6, 7, 8])
        self.assertEqual(state["owned_blocks"], {"6": 1, "7": 2, "8": 2})
        self.assertEqual(
            state["root_names"], {"fixture": 2, "fsalloc_state": 3}
        )
        self.assertEqual(state["reachable_inodes"], [1, 2, 3])
        self.assertEqual(state["reachable_blocks"], [6, 7, 8])
        self.assertEqual(state["owner_without_bitmap"], [])
        self.assertEqual(state["orphan_blocks"], [])
        self.assertEqual(state["inode_owner_entries"]["2"]["state"], "LIVE_LOW")
        self.assertEqual(state["inode_owner_state_counts"]["LIVE_LOW"], 3)

    def test_diff_reports_exact_bitmap_owner_and_inode_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            before_path = Path(tmp) / "before.img"
            after_path = Path(tmp) / "after.img"
            self.make_image(before_path)
            after = bytearray(before_path.read_bytes())
            after[4 * MODULE.BLOCK_SIZE + 9 // 8] |= 1 << (9 % 8)
            struct.pack_into("<I", after, 5 * MODULE.BLOCK_SIZE + 9 * 4, 2)
            self.write_inode(after, 2, inode_type=2, size=2048, addrs=[7, 9], owner=2)
            after_path.write_bytes(after)
            diff = MODULE.diff_snapshots(
                MODULE.read_snapshot(before_path), MODULE.read_snapshot(after_path)
            )
        self.assertEqual(diff["bitmap_set"], [9])
        self.assertEqual(diff["bitmap_cleared"], [])
        self.assertEqual(
            diff["owner_changes"],
            [
                {
                    "block": 9,
                    "before": 0,
                    "before_state": "FREE",
                    "before_payload": 0,
                    "after": 2,
                    "after_state": "LIVE_LOW",
                    "after_payload": 2,
                }
            ],
        )
        self.assertEqual([entry["inum"] for entry in diff["inode_changes"]], [2])

    def test_snapshot_preserves_qmap_only_crash_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 10 * 4, 2)
            image.write_bytes(raw)
            state = MODULE.read_snapshot(image)
        self.assertEqual(state["owner_without_bitmap"], [10])
        self.assertNotIn(10, state["allocated_blocks"])
        self.assertEqual(state["owned_blocks"]["10"], 2)
        self.assertIn("LIVE_LOW owner has a clear bitmap bit", state["canonical_violations"][0])

    def test_canonical_four_state_decoding_allows_both_transition_halves(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            allocating = (MODULE.OWNER_STATE_ALLOCATING << 30) | 2
            freeing = (MODULE.OWNER_STATE_FREEING << 30) | 3
            struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 9 * 4, allocating)
            struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 10 * 4, freeing)
            raw[4 * MODULE.BLOCK_SIZE + 10 // 8] |= 1 << (10 % 8)
            image.write_bytes(raw)
            state = MODULE.read_snapshot(image)
        self.assertEqual(state["qmap_entries"]["9"]["state"], "ALLOCATING")
        self.assertEqual(state["qmap_entries"]["9"]["top_state"], "ALLOCATING")
        self.assertEqual(state["qmap_entries"]["9"]["bitmap"], 0)
        self.assertEqual(state["qmap_entries"]["9"]["transition_phase"], "INTENT")
        self.assertEqual(state["qmap_entries"]["10"]["state"], "FREEING")
        self.assertEqual(state["qmap_entries"]["10"]["bitmap"], 1)
        self.assertEqual(state["qmap_entries"]["10"]["transition_phase"], "INTENT")
        self.assertEqual(state["canonical_violations"], [])
        self.assertEqual(state["qmap_top_state_counts"]["ALLOCATING"], 1)
        self.assertEqual(state["qmap_top_state_counts"]["FREEING"], 1)

    def test_canonical_validation_rejects_zero_transition_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            struct.pack_into(
                "<I",
                raw,
                5 * MODULE.BLOCK_SIZE + 9 * 4,
                MODULE.OWNER_STATE_ALLOCATING << 30,
            )
            image.write_bytes(raw)
            state = MODULE.read_snapshot(image)
        self.assertEqual(
            state["canonical_violations"], ["block 9: ALLOCATING has a zero payload"]
        )

    def test_inode_owner_uses_the_same_four_state_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            self.write_free_transition_inode(
                raw, 4, (MODULE.OWNER_STATE_ALLOCATING << 30) | 2
            )
            image.write_bytes(raw)
            state = MODULE.read_snapshot(image)
        self.assertEqual(state["inode_owner_entries"]["4"]["state"], "ALLOCATING")
        self.assertEqual(
            state["inode_owner_entries"]["4"]["transition_phase"], "INTENT"
        )
        self.assertEqual(state["canonical_violations"], [])

    def test_rejects_wrong_magic_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            struct.pack_into("<I", raw, MODULE.BLOCK_SIZE, 0)
            image.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "magic"):
                MODULE.read_snapshot(image)
            self.make_image(image)
            image.write_bytes(image.read_bytes()[:-1])
            with self.assertRaisesRegex(MODULE.ImageError, "truncated"):
                MODULE.read_snapshot(image)

    def test_case_expectation_table_closes_all_36_cases(self):
        expected_keys = {
            (operation, phase, action)
            for operation, phases in MODULE.SUPPORTED_PHASES.items()
            for phase in phases
            for action in MODULE.ACTIONS
        }
        self.assertEqual(set(MODULE.CASE_EXPECTATIONS), expected_keys)
        self.assertEqual(len(MODULE.CASE_EXPECTATIONS), 36)
        for action in MODULE.ACTIONS:
            self.assertEqual(
                sum(key[2] == action for key in MODULE.CASE_EXPECTATIONS), 12
            )
        for expectation in MODULE.CASE_EXPECTATIONS.values():
            self.assertEqual(
                set(expectation),
                {
                    "qmap_checkpoint",
                    "inode_checkpoint",
                    "alloc_committed",
                    "fault_new_block_source",
                    "fault_new_inode_source",
                    "reboot_block_delta",
                    "reboot_inode_delta",
                },
            )
        self.assertEqual(
            sum(
                expectation["qmap_checkpoint"] is not None
                for expectation in MODULE.CASE_EXPECTATIONS.values()
            ),
            8,
        )
        self.assertEqual(
            sum(
                expectation["inode_checkpoint"] is not None
                for expectation in MODULE.CASE_EXPECTATIONS.values()
            ),
            6,
        )
        self.assertEqual(
            {
                source: sum(
                    expectation["fault_new_block_source"] == source
                    for expectation in MODULE.CASE_EXPECTATIONS.values()
                )
                for source in {"none", "qmap_transition", "target_inode", "orphan"}
            },
            {"none": 29, "qmap_transition": 4, "target_inode": 2, "orphan": 1},
        )
        self.assertEqual(
            {
                source: sum(
                    expectation["fault_new_inode_source"] == source
                    for expectation in MODULE.CASE_EXPECTATIONS.values()
                )
                for source in {"none", "target_name", "orphan"}
            },
            {"none": 24, "target_name": 9, "orphan": 3},
        )

    def test_case_verifier_rejects_owner_only_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            fault = bytearray(paths[1].read_bytes())
            struct.pack_into("<I", fault, 5 * MODULE.BLOCK_SIZE + 7 * 4, 1)
            paths[1].write_bytes(fault)
            with self.assertRaises(MODULE.ImageError):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_unaffected_payload_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            fault = bytearray(paths[1].read_bytes())
            fault[7 * MODULE.BLOCK_SIZE] ^= 0x5A
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(MODULE.ImageError, "unaffected payload"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_unaffected_inode_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            fault = bytearray(paths[1].read_bytes())
            inode_offset = 2 * MODULE.BLOCK_SIZE + 2 * MODULE.DINODE_SIZE
            struct.pack_into("<h", fault, inode_offset + 2, 1)
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(MODULE.ImageError, "unaffected raw inode"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_root_owner_and_size_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 116, 2)
                path.write_bytes(raw)
            with self.assertRaises(MODULE.ImageError):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 8, 272)
                path.write_bytes(raw)
            with self.assertRaisesRegex(
                MODULE.ImageError, "root inode fingerprint|mountable root template"
            ):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_extra_stable_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            fault = bytearray(paths[1].read_bytes())
            fault[4 * MODULE.BLOCK_SIZE + 9 // 8] |= 1 << (9 % 8)
            struct.pack_into("<I", fault, 5 * MODULE.BLOCK_SIZE + 9 * 4, 2)
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(
                MODULE.ImageError, "fault block identities|bitmap/qmap"
            ):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_wrong_transition_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_free_crash_triplet(Path(tmp))
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "free", "intent", "crash"
                )["verified"]
            )
            wrong_fault = copy.deepcopy(snapshots[1])
            wrong_fault["qmap_entries"]["9"]["block"] = 7
            with self.assertRaisesRegex(MODULE.ImageError, "semantic hash mismatch"):
                MODULE._verify_case_snapshots(
                    snapshots[0],
                    wrong_fault,
                    snapshots[2],
                    "free",
                    "intent",
                    "crash",
                )

    def test_snapshot_envelope_rejects_forged_provenance(self):
        self.assertFalse(hasattr(MODULE, "verify_case"))
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            mutations = (
                ("generator", {"name": "handwritten", "version": "2"}),
                ("image", {"bytes": 1, "sha256": "0" * 64}),
                ("state_sha256", "HANDWRITTEN"),
            )
            for field, value in mutations:
                with self.subTest(field=field):
                    forged = copy.deepcopy(snapshots[1])
                    forged[field] = value
                    with self.assertRaisesRegex(
                        MODULE.ImageError,
                        "generator mismatch|provenance is invalid|semantic hash mismatch",
                    ):
                        MODULE._verify_case_snapshots(
                            snapshots[0],
                            forged,
                            snapshots[2],
                            "ialloc",
                            "intent",
                            "busy",
                        )

    def test_raw_verifier_rejects_every_unlisted_image_region(self):
        mutations = (
            ("boot-block", 0),
            ("superblock-padding", MODULE.BLOCK_SIZE + 100),
            ("root-eof-slack", 6 * MODULE.BLOCK_SIZE + 300),
            ("unallocated-data-block", 9 * MODULE.BLOCK_SIZE + 17),
        )
        for label, offset in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_ialloc_triplet(Path(tmp))
                self.assertTrue(
                    MODULE.verify_case_raw(
                        *paths, "ialloc", "intent", "busy"
                    )["verified"]
                )
                for path in paths[1:]:
                    raw = bytearray(path.read_bytes())
                    raw[offset] ^= 0x5A
                    path.write_bytes(raw)
                with self.assertRaisesRegex(
                    MODULE.ImageError, "outside exact allocator case bytes"
                ):
                    MODULE.verify_case_raw(
                        *paths, "ialloc", "intent", "busy"
                    )

    def test_raw_verifier_rejects_uncommitted_alloc_block_content(self):
        for action in ("busy", "eio"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_alloc_triplet(Path(tmp), "none")
                self.assertTrue(
                    MODULE.verify_case_raw(
                        *paths, "alloc", "intent", action
                    )["verified"]
                )
                for path in paths[1:]:
                    raw = bytearray(path.read_bytes())
                    raw[9 * MODULE.BLOCK_SIZE : 9 * MODULE.BLOCK_SIZE + 4] = b"LEAK"
                    path.write_bytes(raw)
                with self.assertRaisesRegex(
                    MODULE.ImageError,
                    "exact durable content|outside exact allocator case bytes",
                ):
                    MODULE.verify_case_raw(
                        *paths, "alloc", "intent", action
                    )

            with self.subTest(
                action=action, before="dirty-free"
            ), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_alloc_triplet(Path(tmp), "none")
                before = bytearray(paths[0].read_bytes())
                before[9 * MODULE.BLOCK_SIZE : 9 * MODULE.BLOCK_SIZE + 4] = b"OLD!"
                paths[0].write_bytes(before)
                self.assertTrue(
                    MODULE.verify_case_raw(
                        *paths, "alloc", "intent", action
                    )["verified"]
                )

    def test_raw_cli_is_path_bound_and_emits_stable_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            argv = [
                "verify-case-raw",
                *(str(path) for path in paths),
                "--operation",
                "ialloc",
                "--phase",
                "intent",
                "--action",
                "busy",
            ]
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(MODULE.main(argv), 0)
            self.assertTrue(json.loads(output.getvalue())["verified"])

            output_path = Path(tmp) / "verified.json"
            redirected = io.StringIO()
            with redirect_stdout(redirected):
                self.assertEqual(
                    MODULE.main(argv + ["--output", str(output_path)]), 0
                )
            self.assertEqual(redirected.getvalue(), "")
            written = output_path.read_bytes()
            canonical = (
                json.dumps(json.loads(written), sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            self.assertEqual(written, canonical)
            self.assertNotIn(b"\r\n", written)

            raw = bytearray(paths[1].read_bytes())
            raw[0] ^= 1
            paths[1].write_bytes(raw)
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(MODULE.main(argv), 2)
            lines = errors.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            error = json.loads(lines[0])["error"]
            self.assertEqual(error["code"], "FS_ALLOCATOR_IMAGE_INVALID")
            self.assertNotIn(str(Path(tmp)), error["message"])

    def test_raw_verifier_rejects_reserved_metadata_drift(self):
        mutations = (
            ("reserved-qmap", 5 * MODULE.BLOCK_SIZE),
            ("inode-zero", 2 * MODULE.BLOCK_SIZE + 64),
        )
        for label, offset in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_ialloc_triplet(Path(tmp))
                for path in paths[1:]:
                    raw = bytearray(path.read_bytes())
                    raw[offset] ^= 1
                    path.write_bytes(raw)
                with self.assertRaises(MODULE.ImageError):
                    MODULE.verify_case_raw(
                        *paths, "ialloc", "intent", "busy"
                    )

    def test_raw_verifier_rejects_synchronized_public_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths:
                raw = bytearray(path.read_bytes())
                self.write_inode(
                    raw, 1, inode_type=1, size=256, addrs=[6], owner=2
                )
                struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 6 * 4, 2)
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "mountable root template"):
                MODULE.verify_case_raw(
                    *paths, "ialloc", "intent", "busy"
                )

    def test_raw_verifier_accepts_unlinked_ialloc_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_crash_triplet(Path(tmp), "owner")
            result = MODULE.verify_case_raw(
                *paths, "ialloc", "owner", "crash"
            )
            self.assertTrue(result["verified"])
            self.assertEqual(result["expected_manifest"]["first_free_inode"], 4)
            fault = bytearray(paths[1].read_bytes())
            inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
            struct.pack_into("<I", fault, inode_offset + 12, 7)
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(MODULE.ImageError, "exact empty file"):
                MODULE.verify_case_raw(
                    *paths, "ialloc", "owner", "crash"
                )

    def test_raw_verifier_rejects_free_target_payload_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_free_crash_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                raw[9 * MODULE.BLOCK_SIZE + 77] ^= 1
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "payload changed"):
                MODULE.verify_case_raw(
                    *paths, "free", "intent", "crash"
                )

    def test_case_verifier_rejects_missing_or_moved_stage_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            before = bytearray(paths[0].read_bytes())
            self.set_stage(before, "X")
            paths[0].write_bytes(before)
            with self.assertRaisesRegex(MODULE.ImageError, "exact P stage"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                self.write_inode(
                    raw, 3, inode_type=2, size=1, addrs=[10], owner=2
                )
                raw[4 * MODULE.BLOCK_SIZE + 8 // 8] &= ~(1 << (8 % 8))
                struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 8 * 4, 0)
                raw[4 * MODULE.BLOCK_SIZE + 10 // 8] |= 1 << (10 % 8)
                struct.pack_into("<I", raw, 5 * MODULE.BLOCK_SIZE + 10 * 4, 2)
                raw[10 * MODULE.BLOCK_SIZE] = ord("F")
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "stage receipt"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_validate_returns_failure_for_transition_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            output = Path(tmp) / "validate.json"
            self.make_image(image)
            raw = bytearray(image.read_bytes())
            struct.pack_into(
                "<I",
                raw,
                5 * MODULE.BLOCK_SIZE + 9 * 4,
                (MODULE.OWNER_STATE_ALLOCATING << 30) | 2,
            )
            image.write_bytes(raw)
            with redirect_stdout(io.StringIO()):
                status = MODULE.main(
                    ["validate", str(image), "--output", str(output)]
                )
            self.assertEqual(status, 1)
            self.assertEqual(len(json.loads(output.read_text())["transitions"]), 1)

    def test_case_verifier_rejects_free_inode_raw_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 112, 0xDEADBEEF)
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "raw inode table"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_superblock_policy_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                fields = list(
                    struct.unpack_from(
                        MODULE.SUPERBLOCK_FORMAT, raw, MODULE.BLOCK_SIZE
                    )
                )
                fields[8] += 1
                fields[15] = MODULE.storage_policy_checksum(
                    (
                        fields[8],
                        fields[9],
                        fields[14],
                        fields[10],
                        fields[11],
                        fields[12],
                        fields[13],
                    )
                )
                struct.pack_into(
                    MODULE.SUPERBLOCK_FORMAT, raw, MODULE.BLOCK_SIZE, *fields
                )
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "geometry|policy contract"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_root_dirent_reordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                offset = 6 * MODULE.BLOCK_SIZE
                first = bytes(raw[offset : offset + 16])
                second = bytes(raw[offset + 16 : offset + 32])
                raw[offset : offset + 16] = second
                raw[offset + 16 : offset + 32] = first
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "ordered root dirent"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_stage_block_slack_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                raw[8 * MODULE.BLOCK_SIZE + 1] = 0xA5
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "trailing data"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_mutated_alloc_target_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_alloc_triplet(Path(tmp), "none")
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "alloc", "intent", "busy"
                )["verified"]
            )
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 92, 0x1234)
                self.recompute_inode_checksum(raw, 4)
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "public VFS label"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "alloc",
                    "intent",
                    "busy",
                )

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_alloc_triplet(Path(tmp), "none")
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 112, 999)
                self.recompute_inode_checksum(raw, 4)
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "wrong incarnation"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "alloc",
                    "intent",
                    "busy",
                )

    def test_case_verifier_rejects_new_ialloc_template_drift(self):
        for phase in ("intent", "owner"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_ialloc_crash_triplet(Path(tmp), phase)
                fault = bytearray(paths[1].read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                struct.pack_into("<I", fault, inode_offset + 64, 0xFEEDFACE)
                paths[1].write_bytes(fault)
                with self.assertRaisesRegex(
                    MODULE.ImageError, "allocation template|public VFS label"
                ):
                    MODULE._verify_case_snapshots(
                        *(MODULE.read_snapshot(path) for path in paths),
                        "ialloc",
                        phase,
                        "crash",
                    )

    def test_case_verifier_requires_exact_owner_busy_retirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_owner_busy_triplet(Path(tmp))
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            verified = MODULE._verify_case_snapshots(
                *snapshots,
                "ialloc",
                "owner",
                "busy",
            )
            self.assertEqual(verified["format"], MODULE.VERIFIED_FORMAT)
            self.assertEqual(verified["generator"], MODULE.GENERATOR)
            self.assertEqual(
                verified["images"],
                {
                    "before": snapshots[0]["image"],
                    "fault": snapshots[1]["image"],
                    "reboot": snapshots[2]["image"],
                },
            )
            self.assertEqual(verified["expected_manifest"]["retired_ialloc_attempts"], [4])

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_triplet(Path(tmp))
            with self.assertRaisesRegex(MODULE.ImageError, "invalid free template"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "owner",
                    "busy",
                )

    def test_case_verifier_requires_exact_free_target_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_free_crash_triplet(Path(tmp))
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "free", "intent", "crash"
                )["verified"]
            )
            for path in paths[1:]:
                raw = bytearray(path.read_bytes())
                inode_offset = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                struct.pack_into("<I", raw, inode_offset + 8, 0xDEADBEEF)
                struct.pack_into("<I", raw, inode_offset + 12, 1)
                struct.pack_into("<I", raw, inode_offset + 64, 0xCAFEBABE)
                path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "invalid free template"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "free",
                    "intent",
                    "crash",
                )

    def test_case_verifier_closes_all_nine_ifree_fault_templates(self):
        for phase in ("intent", "owner", "refund"):
            for action in MODULE.ACTIONS:
                with self.subTest(
                    phase=phase, action=action
                ), tempfile.TemporaryDirectory() as tmp:
                    paths = self.make_ifree_triplet(Path(tmp), phase, action)
                    snapshots = [MODULE.read_snapshot(path) for path in paths]
                    self.assertTrue(
                        MODULE._verify_case_snapshots(
                            *snapshots, "ifree", phase, action
                        )["verified"]
                    )
                    fault = bytearray(paths[1].read_bytes())
                    inode_offset = (
                        2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
                    )
                    struct.pack_into("<I", fault, inode_offset + 64, 0xFEEDFACE)
                    paths[1].write_bytes(fault)
                    with self.assertRaisesRegex(
                        MODULE.ImageError, "exact phase template"
                    ):
                        MODULE._verify_case_snapshots(
                            *(MODULE.read_snapshot(path) for path in paths),
                            "ifree",
                            phase,
                            action,
                        )

    def test_case_verifier_rejects_extra_or_moved_ialloc_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_crash_triplet(Path(tmp), "intent")
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "ialloc", "intent", "crash"
                )["verified"]
            )
            fault = bytearray(paths[1].read_bytes())
            self.write_inode(fault, 5, inode_type=2, size=0, addrs=[], owner=2)
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(MODULE.ImageError, "fault inode identities"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "crash",
                )

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_ialloc_crash_triplet(Path(tmp), "intent")
            fault = bytearray(paths[1].read_bytes())
            inode4 = 2 * MODULE.BLOCK_SIZE + 4 * MODULE.DINODE_SIZE
            fault[inode4 : inode4 + MODULE.DINODE_SIZE] = b"\0" * MODULE.DINODE_SIZE
            self.write_free_transition_inode(
                fault, 5, (MODULE.OWNER_STATE_ALLOCATING << 30) | 2
            )
            struct.pack_into(
                "<h",
                fault,
                2 * MODULE.BLOCK_SIZE + 5 * MODULE.DINODE_SIZE,
                2,
            )
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(MODULE.ImageError, "first free inode"):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "ialloc",
                    "intent",
                    "crash",
                )

    def test_case_verifier_rejects_moved_or_extra_alloc_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_alloc_triplet(Path(tmp), "allocating")
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "alloc", "intent", "crash"
                )["verified"]
            )
            fault = bytearray(paths[1].read_bytes())
            struct.pack_into("<I", fault, 5 * MODULE.BLOCK_SIZE + 9 * 4, 0)
            struct.pack_into(
                "<I",
                fault,
                5 * MODULE.BLOCK_SIZE + 10 * 4,
                (MODULE.OWNER_STATE_ALLOCATING << 30) | 2,
            )
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(
                MODULE.ImageError, "first free block|bitmap/qmap"
            ):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "alloc",
                    "intent",
                    "crash",
                )

        for checkpoint, phase in (("allocating", "intent"), ("orphan", "owner")):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as tmp:
                paths = self.make_alloc_triplet(Path(tmp), checkpoint)
                fault = bytearray(paths[1].read_bytes())
                fault[9 * MODULE.BLOCK_SIZE : 9 * MODULE.BLOCK_SIZE + 4] = b"LEAK"
                paths[1].write_bytes(fault)
                with self.assertRaisesRegex(MODULE.ImageError, "durable content"):
                    MODULE._verify_case_snapshots(
                        *(MODULE.read_snapshot(path) for path in paths),
                        "alloc",
                        phase,
                        "crash",
                    )

        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_alloc_triplet(Path(tmp), "orphan")
            snapshots = [MODULE.read_snapshot(path) for path in paths]
            self.assertTrue(
                MODULE._verify_case_snapshots(
                    *snapshots, "alloc", "owner", "crash"
                )["verified"]
            )
            fault = bytearray(paths[1].read_bytes())
            fault[4 * MODULE.BLOCK_SIZE + 10 // 8] |= 1 << (10 % 8)
            struct.pack_into("<I", fault, 5 * MODULE.BLOCK_SIZE + 10 * 4, 2)
            paths[1].write_bytes(fault)
            with self.assertRaisesRegex(
                MODULE.ImageError, "fault block identities|bitmap/qmap"
            ):
                MODULE._verify_case_snapshots(
                    *(MODULE.read_snapshot(path) for path in paths),
                    "alloc",
                    "owner",
                    "crash",
                )

    def test_case_verifier_rejects_unexpected_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            before_path = Path(tmp) / "before.img"
            fault_path = Path(tmp) / "fault.img"
            reboot_path = Path(tmp) / "reboot.img"
            self.make_image(before_path)
            fault_raw = bytearray(before_path.read_bytes())
            self.set_stage(fault_raw, "F")
            fault_path.write_bytes(fault_raw)
            reboot_path.write_bytes(fault_raw)
            verified = MODULE._verify_case_snapshots(
                MODULE.read_snapshot(before_path),
                MODULE.read_snapshot(fault_path),
                MODULE.read_snapshot(reboot_path),
                "ialloc",
                "intent",
                "busy",
            )
            self.assertTrue(verified["verified"])

            raw = bytearray(fault_path.read_bytes())
            struct.pack_into(
                "<I",
                raw,
                5 * MODULE.BLOCK_SIZE + 9 * 4,
                (MODULE.OWNER_STATE_ALLOCATING << 30) | 2,
            )
            fault_path.write_bytes(raw)
            with self.assertRaisesRegex(MODULE.ImageError, "qmap transition count"):
                MODULE._verify_case_snapshots(
                    MODULE.read_snapshot(before_path),
                    MODULE.read_snapshot(fault_path),
                    MODULE.read_snapshot(reboot_path),
                    "ialloc",
                    "intent",
                    "busy",
                )


if __name__ == "__main__":
    unittest.main()
