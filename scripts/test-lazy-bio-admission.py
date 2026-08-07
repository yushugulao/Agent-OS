#!/usr/bin/env python3
"""缓存未命中触发 BIO 准入的变异测试。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-lazy-bio-admission.py"
FILES = (
    "io_policy.h",
    "os/bio.h",
    "os/bio.c",
    "os/main.c",
    "os/proc.h",
    "os/syscall.c",
    "os/fs.c",
    "user/src/iobudget_ucore.c",
)


class LazyBioAdmissionTests(unittest.TestCase):
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

    def test_rejects_eager_lazy_begin(self) -> None:
        self.mutate(
            "os/bio.c",
            "thread->io_request_flags = BIO_REQUEST_LAZY;",
            "io_active_request_acquire(state);\n"
            "\tthread->io_request_flags = BIO_REQUEST_LAZY;",
        )
        self.assert_rejected("lazy begin still reserves")

    def test_rejects_upgrade_with_buffer_hold(self) -> None:
        self.mutate(
            "os/bio.c",
            "thread->bio_buffer_holds != 0 || thread->bio_fs_atomic_depth != 0",
            "thread->bio_buffer_holds == 0 || thread->bio_fs_atomic_depth != 0",
        )
        self.assert_rejected("not atomic and fail closed")

    def test_rejects_upgrade_after_buffer_acquire(self) -> None:
        self.mutate(
            "os/bio.c",
            "if (b == 0 || !b->valid) {",
            "if (0) {",
        )
        self.assert_rejected("does not distinguish valid cache hits")

    def test_rejects_batch_without_preflight(self) -> None:
        self.mutate(
            "os/bio.c",
            "result = bio_cache_batch_preflight(dev, blocknos, count);",
            "result = VIRTIO_DISK_OK;",
        )
        self.assert_rejected("hold a prefix before admission")

    def test_rejects_eager_read_syscall(self) -> None:
        self.mutate(
            "os/syscall.c",
            "bio_request_begin_current_lazy();",
            "bio_request_begin_current();",
        )
        self.assert_rejected("do not select lazy admission")

    def test_rejects_read_no_sleep_marker(self) -> None:
        self.mutate(
            "os/fs.c",
            "result = readi_atomic(ip, cred, lease, user_dst, dst, off, n, 0);",
            "bio_fs_atomic_enter();\n"
            "\tresult = readi_atomic(ip, cred, lease, user_dst, dst, off, n, 0);",
        )
        self.assert_rejected("readi_with_auth keeps an unnecessary")

    def test_rejects_unadmitted_physical_write(self) -> None:
        self.mutate(
            "os/bio.c",
            "if (!bio_request_active_current())\n"
            "\t\treturn VIRTIO_DISK_ERR_BUSY;",
            "if (0)\n\t\treturn VIRTIO_DISK_ERR_BUSY;",
        )
        self.assert_rejected("physical writes can bypass")

    def test_rejects_runtime_policy_before_boot_io(self) -> None:
        self.mutate(
            "os/main.c",
            "\tload_init_app();\n"
            "\tinfof(\"start scheduler!\");\n"
            "\t/* Runtime I/O admission starts after boot-only image loading completes. */\n"
            "\tbio_policy_start();",
            "\tbio_policy_start();\n"
            "\tload_init_app();\n"
            "\tinfof(\"start scheduler!\");",
        )
        self.assert_rejected("runtime I/O admission starts before")

    def test_rejects_guest_without_reservation_assertion(self) -> None:
        self.mutate(
            "user/src/iobudget_ucore.c",
            "after.leased == before.leased",
            "after.leased >= before.leased",
        )
        self.assert_rejected("Guest regression does not prove")

    def test_rejects_guest_without_cache_ready_precondition(self) -> None:
        self.mutate(
            "user/src/iobudget_ucore.c",
            "cache_ready = 1;",
            "cache_ready = 0;",
        )
        self.assert_rejected("Guest regression does not prove")


if __name__ == "__main__":
    unittest.main()
