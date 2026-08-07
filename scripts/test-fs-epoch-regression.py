#!/usr/bin/env python3
"""fs_epoch 动态回归预期的纯 Host 变异测试。"""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


image = load_module("verify_fs_epoch_image", ROOT / "scripts/verify-fs-epoch-image.py")
log = load_module("verify_fs_epoch_log", ROOT / "scripts/verify-fs-epoch-log.py")


def seal(snapshot: dict[str, object]) -> dict[str, object]:
    snapshot.pop("image", None)
    snapshot.pop("generator", None)
    snapshot.pop("state_sha256", None)
    snapshot["format"] = image.SNAPSHOT_FORMAT
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    snapshot["state_sha256"] = image.digest(canonical)
    snapshot["image"] = {
        "bytes": int(snapshot["geometry"]["size"]) * image.BLOCK_SIZE,
        "sha256": image.digest(canonical + b"image"),
    }
    snapshot["generator"] = dict(image.SNAPSHOT_GENERATOR)
    return snapshot


def snapshot(*, batch: bytes, stage: bytes, created: bool) -> dict[str, object]:
    root_names = {
        image.BATCH_PATH: 10,
        image.STATE_PATH: 11,
        "sentinel": 13,
    }
    payloads = {
        "10": image.digest(batch),
        "11": image.digest(stage),
        "13": image.digest(b"sentinel"),
    }
    inode_blocks = {
        "1": [90],
        "10": list(range(100, 100 + image.BATCH_BLOCKS)),
        "11": [120],
        "13": [122],
    }
    block_hashes = {
        str(100 + index): image.digest(
            batch[index * image.BLOCK_SIZE : (index + 1) * image.BLOCK_SIZE]
        )
        for index in range(image.BATCH_BLOCKS)
    }
    block_hashes.update(
        {
            "90": image.digest(b"root-created" if created else b"root-base"),
            "120": image.digest(stage + bytes(image.BLOCK_SIZE - 1)),
            "122": image.digest(b"sentinel" + bytes(image.BLOCK_SIZE - 8)),
        }
    )
    allocated = [90, *range(100, 100 + image.BATCH_BLOCKS), 120, 122]
    inodes = {
        "1": {"size": 64 if created else 48, "vfs_policy": 4},
        "10": {"size": len(batch), "vfs_policy": 2},
        "11": {"size": 1, "vfs_policy": 2},
        "13": {"size": 8, "vfs_policy": 1},
    }
    if created:
        root_names[image.CREATED_PATH] = 12
        payloads["12"] = image.CREATED_NEW_HASH
        inode_blocks["12"] = [121]
        block_hashes["121"] = image.digest(image.CREATED_NEW)
        allocated.append(121)
        inodes["12"] = {"size": image.BLOCK_SIZE, "vfs_policy": 2}

    inode_raw = {str(inum): image.digest(f"free:{inum}".encode()) for inum in range(16)}
    inode_raw["1"] = image.digest(b"root-created" if created else b"root-base")
    inode_raw["10"] = image.digest(b"batch-inode")
    inode_raw["11"] = image.digest(b"state-inode")
    inode_raw["13"] = image.digest(b"sentinel-inode")
    if created:
        inode_raw["12"] = image.digest(b"created-inode")
    incarnations = {str(inum): 1 for inum in range(16)}
    if created:
        incarnations["12"] = 2
    owned = {str(block): 2 for block in allocated}
    qmap = {key: {"raw": 2, "state": "LIVE_LOW"} for key in owned}
    owner_inums = (1, 10, 11, 13, 12) if created else (1, 10, 11, 13)
    inode_owners = {
        str(inum): {"raw": 2, "state": "LIVE_LOW"}
        for inum in owner_inums
    }
    value: dict[str, object] = {
        "geometry": {
            "size": 256,
            "nblocks": 220,
            "ninodes": 16,
            "datastart": 20,
            "superblock_sha256": image.digest(b"superblock"),
        },
        "root_names": root_names,
        "payload_sha256": payloads,
        "inode_blocks": inode_blocks,
        "block_sha256": block_hashes,
        "nonzero_data_block_sha256": dict(block_hashes),
        "canonical_violations": [],
        "allocated_blocks": sorted(allocated),
        "owned_blocks": owned,
        "qmap_entries": qmap,
        "inode_owner_entries": inode_owners,
        "inode_raw_sha256": inode_raw,
        "inode_incarnations": incarnations,
        "inodes": inodes,
        "reachable_inodes": sorted(int(value) for value in root_names.values()),
        "reachable_blocks": sorted(allocated),
        "orphan_inodes": [],
        "orphan_blocks": [],
        "allocated_unowned": [],
        "owner_without_bitmap": [],
    }
    return seal(value)


class FsEpochImageTests(unittest.TestCase):
    def setUp(self):
        self.before = snapshot(batch=image.BATCH_OLD, stage=b"P", created=False)
        self.complete = snapshot(batch=image.BATCH_NEW, stage=b"D", created=True)

    def mixed_fault(self) -> dict[str, object]:
        mixed = bytearray(image.BATCH_OLD)
        mixed[: 3 * image.BLOCK_SIZE] = image.BATCH_NEW[: 3 * image.BLOCK_SIZE]
        return snapshot(batch=bytes(mixed), stage=b"R", created=False)

    def test_dirty_and_durable_outcomes(self):
        dirty_fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        durable_fault = snapshot(batch=image.BATCH_NEW, stage=b"R", created=True)
        dirty = image.verify(
            "dirty", self.before, dirty_fault, self.complete, self.complete
        )
        durable = image.verify(
            "durable", self.before, durable_fault, self.complete, self.complete
        )
        self.assertEqual(dirty["fault_old_blocks"], image.BATCH_BLOCKS)
        self.assertEqual(durable["fault_new_blocks"], image.BATCH_BLOCKS)

    def test_inflight_requires_complete_old_or_new_blocks(self):
        fault = self.mixed_fault()
        result = image.verify(
            "inflight",
            self.before,
            fault,
            self.complete,
            self.complete,
            calibration_attempt=2,
            calibration_delay="0.0001s",
        )
        self.assertEqual(result["fault_old_blocks"], 5)
        self.assertEqual(result["fault_new_blocks"], 3)
        self.assertEqual(result["calibration_attempt"], 2)
        torn = copy.deepcopy(fault)
        torn["block_sha256"]["103"] = "0" * 64
        seal(torn)
        with self.assertRaisesRegex(image.VerificationError, "neither complete"):
            image.verify(
                "inflight", self.before, torn, self.complete, self.complete
            )

    def test_inflight_probe_distinguishes_window_miss(self):
        selected = image.probe_inflight(self.before, self.mixed_fault())
        self.assertTrue(selected["selected"])
        old = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        with self.assertRaises(image.InflightWindowMiss) as miss:
            image.probe_inflight(self.before, old)
        self.assertEqual(miss.exception.result["fault_old_blocks"], 8)

    def test_inflight_rejects_arbitrary_canonical_corruption(self):
        fault = self.mixed_fault()
        fault["canonical_violations"] = ["unrelated owner corruption"]
        seal(fault)
        with self.assertRaisesRegex(image.VerificationError, "not canonical"):
            image.verify(
                "inflight", self.before, fault, self.complete, self.complete
            )

    def test_campaign_rejects_namespace_loss_and_replacement(self):
        fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        retry = copy.deepcopy(self.complete)
        final = copy.deepcopy(self.complete)
        del retry["root_names"]["sentinel"]
        del final["root_names"]["sentinel"]
        seal(retry)
        seal(final)
        with self.assertRaisesRegex(
            image.VerificationError, "namespace mismatch|non-target object"
        ):
            image.verify("dirty", self.before, fault, retry, final)

    def test_campaign_rejects_non_target_payload_mutation(self):
        fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        retry = copy.deepcopy(self.complete)
        final = copy.deepcopy(self.complete)
        retry["payload_sha256"]["13"] = image.digest(b"replaced")
        final["payload_sha256"]["13"] = image.digest(b"replaced")
        seal(retry)
        seal(final)
        with self.assertRaisesRegex(image.VerificationError, "non-target payload"):
            image.verify("dirty", self.before, fault, retry, final)

    def test_kernel_private_payload_may_advance_between_boots(self):
        fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        retry = copy.deepcopy(self.complete)
        final = copy.deepcopy(self.complete)
        for candidate, value in (
            (self.before, b"private-before"),
            (fault, b"private-fault"),
            (retry, b"private-retry"),
            (final, b"private-final"),
        ):
            candidate["root_names"][".agentmeta"] = candidate[
                "root_names"
            ].pop("sentinel")
            candidate["inodes"]["13"]["vfs_policy"] = (
                image.VFS_POLICY_KERNEL_PRIVATE
            )
            candidate["payload_sha256"]["13"] = image.digest(value)
            block = value + bytes(image.BLOCK_SIZE - len(value))
            candidate["block_sha256"]["122"] = image.digest(block)
            candidate["nonzero_data_block_sha256"]["122"] = image.digest(block)
            seal(candidate)
        result = image.verify("dirty", self.before, fault, retry, final)
        self.assertEqual(result["case"], "dirty")

    def test_unrecognized_kernel_private_payload_is_preserved(self):
        fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        retry = copy.deepcopy(self.complete)
        final = copy.deepcopy(self.complete)
        for candidate in (self.before, fault, retry, final):
            candidate["inodes"]["13"]["vfs_policy"] = (
                image.VFS_POLICY_KERNEL_PRIVATE
            )
            seal(candidate)
        retry["payload_sha256"]["13"] = image.digest(b"private-replaced")
        seal(retry)
        with self.assertRaisesRegex(image.VerificationError, "non-target payload"):
            image.verify("dirty", self.before, fault, retry, final)

    def test_campaign_rejects_geometry_and_envelope_mutation(self):
        fault = snapshot(batch=image.BATCH_OLD, stage=b"R", created=False)
        bad_geometry = copy.deepcopy(self.complete)
        bad_geometry["geometry"]["nblocks"] += 1
        seal(bad_geometry)
        with self.assertRaisesRegex(image.VerificationError, "geometry"):
            image.verify("dirty", self.before, fault, bad_geometry, bad_geometry)

        bad_hash = copy.deepcopy(self.complete)
        bad_hash["state_sha256"] = "0" * 64
        with self.assertRaisesRegex(image.VerificationError, "semantic hash"):
            image.verify("dirty", self.before, fault, bad_hash, self.complete)


class FsEpochLogTests(unittest.TestCase):
    def receipt(self, kind: str, case: str, *, writes: int = 13, failed: int = 0) -> str:
        return (
            f"fsepoch_ucore: {kind} case={case} payload_blocks=9 "
            f"writes={writes} flushes=3 failed={failed}"
        )

    def valid_logs(self, case: str) -> tuple[list[str], list[str], list[str]]:
        point = {
            "dirty": "before_fsync",
            "inflight": "fsync_enter",
            "durable": "after_fsync",
        }[case]
        marker = f"fsepoch_ucore: powercut_window case={case} point={point}"
        checkpoint = f"fsepoch_ucore: retry_durable_checkpoint case={case}"
        if case == "durable":
            fault = [
                "fsepoch_ucore: commit_fsync_enter case=durable",
                self.receipt("commit_receipt", case),
                marker,
            ]
            retry = [
                "fsepoch_ucore: durable_noop_fsync_enter case=durable",
                "fsepoch_ucore: durable_recovery noop_fsync_io=0",
                checkpoint,
            ]
        else:
            fault = [marker]
            retry = [
                f"fsepoch_ucore: retry_fsync_enter case={case}",
                self.receipt("retry_receipt", case),
                checkpoint,
            ]
            if case == "inflight":
                retry.insert(
                    0,
                    "fsepoch_ucore: inflight_recovery old_blocks=5 new_blocks=3",
                )
        final = [f"fsepoch_ucore: parent passed case={case} blocks=9"]
        return fault, retry, final

    def test_all_case_receipts_and_order(self):
        for case in ("dirty", "inflight", "durable"):
            with self.subTest(case=case):
                log.verify(case, *self.valid_logs(case))

    def test_inflight_rejects_fsync_return(self):
        fault, retry, final = self.valid_logs("inflight")
        fault.append("fsepoch_ucore: fsync_returned case=inflight")
        with self.assertRaisesRegex(log.LogError, "after fsync returned"):
            log.verify("inflight", fault, retry, final)

    def test_receipt_rejects_unbounded_writes_and_failures(self):
        fault, retry, final = self.valid_logs("dirty")
        retry[1] = self.receipt("retry_receipt", "dirty", writes=1_000_000)
        with self.assertRaisesRegex(log.LogError, "invalid measured"):
            log.verify("dirty", fault, retry, final)
        retry[1] = self.receipt("retry_receipt", "dirty", failed=1)
        with self.assertRaisesRegex(log.LogError, "invalid measured"):
            log.verify("dirty", fault, retry, final)

    def test_receipt_rejects_reordered_commit_evidence(self):
        fault, retry, final = self.valid_logs("durable")
        fault[1], fault[2] = fault[2], fault[1]
        with self.assertRaisesRegex(log.LogError, "order is invalid"):
            log.verify("durable", fault, retry, final)

        fault, retry, final = self.valid_logs("dirty")
        retry[1], retry[2] = retry[2], retry[1]
        with self.assertRaisesRegex(log.LogError, "order is invalid"):
            log.verify("dirty", fault, retry, final)


class FsEpochWiringTests(unittest.TestCase):
    def test_dynamic_runner_is_in_acceptance_and_cleans_background_jobs(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full = (ROOT / "scripts/run-full-verification.sh").read_text(encoding="utf-8")
        parallel = (ROOT / "scripts/run-parallel-qemu-regressions.py").read_text(
            encoding="utf-8"
        )
        runner = (ROOT / "scripts/run-fs-epoch-tests.sh").read_text(encoding="utf-8")
        self.assertIn("fs-epoch-test:", makefile)
        self.assertIn("run-fs-epoch-tests.sh", full)
        self.assertIn('RegressionCase("fs-epoch"', parallel)
        self.assertIn("terminate_pending", runner)
        self.assertIn("trap cleanup EXIT", runner)


if __name__ == "__main__":
    unittest.main()
