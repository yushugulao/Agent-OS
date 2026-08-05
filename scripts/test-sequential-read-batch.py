#!/usr/bin/env python3
"""Mutation tests for bounded inode sequential-read batching."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-sequential-read-batch.py"
FILES = (
    "os/fs.c",
    "os/virtio.h",
    "os/virtio_disk.c",
    "os/performance_stats.h",
)


class SequentialReadBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_rejected(self, needle: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_stack_expanding_batch(self) -> None:
        self.mutate("os/fs.c", "#define FS_READ_BATCH_MAX 4U", "#define FS_READ_BATCH_MAX 8U")
        self.assert_rejected("stack-bounded")

    def test_rejects_per_block_submit(self) -> None:
        self.mutate(
            "os/fs.c",
            "failure_result = fs_read_blocks_batch(ip->dev, blocknos, buffers,\n"
            "\t\t\t\t\t\t      batch_count);",
            "failure_result = fs_read_block(ip->dev, blocknos[0], &buffers[0]);",
        )
        self.assert_rejected("multi-block reads bypass")

    def test_rejects_unbounded_mapping(self) -> None:
        self.mutate(
            "os/fs.c",
            "batch_count = MIN(batch_count, batch_limit);",
            "batch_count = batch_count;",
        )
        self.assert_rejected("not bounded by the stack batch width")

    def test_rejects_indirect_map_reacquire(self) -> None:
        self.mutate(
            "os/fs.c",
            "while (mapped < count && index < NINDIRECT) {",
            "while (mapped < count && index < NINDIRECT) {\n"
            "\t\tstruct buf *again;\n"
            "\t\t(void)fs_read_block(ip->dev, ip->addrs[NDIRECT], &again);",
        )
        self.assert_rejected("reacquires the indirect map per data block")

    def test_rejects_lost_error_fallback(self) -> None:
        self.mutate(
            "os/fs.c",
            "failure_result = fs_read_block(ip->dev, blocknos[0],\n"
            "\t\t\t\t\t\t\t       &buffers[0]);",
            "failure_result = -1;",
        )
        self.assert_rejected("batch failure fallback")

    def test_rejects_fallback_that_skips_mapped_prefix(self) -> None:
        self.mutate(
            "os/fs.c",
            "batch_count = 1;\n\t\t\t\tmapping_failed = 0;",
            "batch_count = 1;\n\t\t\t\tmapping_failed = 1;",
        )
        self.assert_rejected("discard a mapped positive prefix")

    def test_rejects_forced_device_batching(self) -> None:
        self.mutate(
            "os/fs.c",
            "batch_limit = device_read ? 1U : FS_READ_BATCH_MAX;",
            "batch_limit = FS_READ_BATCH_MAX;",
        )
        self.assert_rejected("forced device reads can enter")

    def test_rejects_checkpoint_before_release(self) -> None:
        path = self.root / "os/fs.c"
        source = path.read_text(encoding="utf-8")
        release = "\t\t\tbrelse(buffers[copied]);\n"
        checkpoint = "\t\t\tcheckpoint = bio_request_checkpoint();\n"
        self.assertIn(release, source)
        self.assertIn(checkpoint, source)
        source = source.replace(checkpoint, "", 1)
        source = source.replace(release, checkpoint + release, 1)
        path.write_text(source, encoding="utf-8")
        self.assert_rejected("commit, then checkpoint")

    def test_rejects_vm_window_contract_drift(self) -> None:
        self.mutate(
            "os/fs.c",
            "FS_READ_BATCH_MAX * BSIZE <= VM_COPYOUTV_MAX_BYTES",
            "FS_READ_BATCH_MAX * BSIZE > VM_COPYOUTV_MAX_BYTES",
        )
        self.assert_rejected("bounded VM copy window")

    def test_rejects_per_block_copyout_for_multi_block_batch(self) -> None:
        self.mutate(
            "os/fs.c",
            "either_copyoutv(user_dst, dst, segments, batch_count)",
            "either_copyout(user_dst, dst, (char *)segments[0].source, "
            "segments[0].length)",
        )
        self.assert_rejected("bypass the bounded scatter copyout window")

    def test_rejects_commit_before_copyout_failure_check(self) -> None:
        self.mutate(
            "os/fs.c",
            "if (failed)\n\t\t\tbreak;\n\t\ttot += batch_bytes;",
            "tot += batch_bytes;\n\t\tif (failed)\n\t\t\tbreak;",
        )
        self.assert_rejected("copyout failure can commit bytes")

    def test_rejects_checkpoint_inside_batch(self) -> None:
        self.mutate(
            "os/fs.c",
            "segments[copied].length = m;\n\t\t\tbatch_bytes += m;",
            "segments[copied].length = m;\n"
            "\t\t\t(void)bio_request_checkpoint();\n\t\t\tbatch_bytes += m;",
        )
        self.assert_rejected("checkpoint once per completed batch")

    def test_rejects_sparse_prefix_skip(self) -> None:
        self.mutate(
            "os/fs.c",
            "if (mapping_failed) {\n"
            "\t\t\tfailure_result = map_result;\n"
            "\t\t\tfailed = 1;\n"
            "\t\t\tbreak;\n"
            "\t\t}",
            "if (mapping_failed)\n\t\t\tcontinue;",
        )
        self.assert_rejected("sparse mapping")

    def test_rejects_negative_result_after_progress(self) -> None:
        self.mutate(
            "os/fs.c",
            "if (failed && tot == 0)",
            "if (failed)",
        )
        self.assert_rejected("zero-progress failure from a prefix")

    def test_rejects_read_batch_telemetry_alias(self) -> None:
        self.mutate(
            "os/virtio_disk.c",
            "type == VIRTIO_BLK_T_OUT ?\n"
            "\t\t\tKERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH :\n"
            "\t\t\tKERNEL_PERFORMANCE_VIRTIO_READ_BATCH",
            "type == VIRTIO_BLK_T_OUT ?\n"
            "\t\t\tKERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH :\n"
            "\t\t\tKERNEL_PERFORMANCE_VIRTIO_SINGLE",
        )
        self.assert_rejected("read batches are not visible")


if __name__ == "__main__":
    unittest.main()
