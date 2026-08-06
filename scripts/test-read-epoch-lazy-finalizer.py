#!/usr/bin/env python3
"""Mutation tests for the read epoch lazy-finalizer contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-read-epoch-lazy-finalizer.py"
FILES = (
    "os/bio.c",
    "os/bio.h",
    "os/file.c",
    "os/file.h",
    "os/fs.c",
    "os/fs_epoch.c",
    "os/fs_epoch.h",
    "os/syscall.c",
)


class ReadEpochLazyFinalizerTests(unittest.TestCase):
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

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_cannot_take_mutation_epoch(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:",
            "\tcase SYS_read:\n\tcase SYS_write:",
        )
        self.assert_rejected("pure inode reads still acquire")

    def test_read_cannot_bypass_io_admission(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tcase SYS_read:\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
            "\tcase SYS_read:\n\t\treturn 0;\n\tcase SYS_write:\n"
            "\t\treturn transaction->fd_uses_disk;",
        )
        self.assert_rejected("inode reads bypass block-I/O admission")

    def test_final_inode_must_retain_cleanup_token(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_prepare(owner, token)",
            "bio_cleanup_token_prepare_disabled(owner, token)",
        )
        self.assert_rejected("destructive inode close lacks cleanup-token retention")

    def test_prepare_cannot_consume_inode_reference(self) -> None:
        self.mutate(
            "os/file.c",
            "	if (f->type == FD_INODE && f->ip->ref == 1 &&",
            "	(void)iput_drop_only(f->ip);\n"
            "	if (f->type == FD_INODE && f->ip->ref == 1 &&",
        )
        self.assert_rejected("before unified settlement")

    def test_cleanup_token_must_be_destructive_only(self) -> None:
        self.mutate(
            "os/file.c",
            "f->type == FD_INODE && f->ip->ref == 1 &&\n"
            "	    f->ip->valid && f->ip->removed &&",
            "f->type == FD_INODE &&",
        )
        self.assert_rejected("not limited to destructive")

    def test_inode_reference_must_reach_settlement(self) -> None:
        self.mutate(
            "os/file.c",
            "receipt->ip = f->ip;",
            "receipt->ip = 0;",
        )
        self.assert_rejected("not transferred to settlement")

    def test_cleanup_token_must_precede_final_detach(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (f->type == FD_INODE && f->ip->ref == 1 &&",
            "\tf->ref = 0;\n"
            "\tif (f->type == FD_INODE && f->ip->ref == 1 &&",
        )
        self.assert_rejected("cleanup retention does not precede")

    def test_cleanup_failure_cannot_shift_to_system_owner(self) -> None:
        self.mutate(
            "os/file.c",
            "return bio_cleanup_token_prepare(owner, token);",
            "if (bio_cleanup_token_prepare(owner, token) < 0)\n"
            "\t\treturn bio_cleanup_token_prepare(FS_OWNER_SYSTEM, token);\n"
            "\treturn 0;",
        )
        self.assert_rejected("launder work to the system owner")

    def test_file_object_must_capture_cleanup_owner(self) -> None:
        self.mutate(
            "os/file.c",
            "f->cleanup_owner = bio_process_owner(owner);",
            "f->cleanup_owner = FS_OWNER_PUBLIC;",
        )
        self.assert_rejected("does not retain its allocating I/O principal")

    def test_receipt_must_retain_cleanup_owner(self) -> None:
        self.mutate(
            "os/file.c",
            "receipt->cleanup_owner = f->cleanup_owner;",
            "receipt->cleanup_owner = FS_OWNER_SYSTEM;",
        )
        self.assert_rejected("not transferred to settlement")

    def test_late_destructive_close_must_retain_cleanup_token(self) -> None:
        self.mutate(
            "os/file.c",
            "fileclose_cleanup_token_prepare(\n"
            "\t\t    receipt->cleanup_owner, &receipt->cleanup_token) < 0",
            "0",
        )
        self.assert_rejected("late destructive classification")

    def test_drop_only_fast_path_is_mandatory(self) -> None:
        self.mutate(
            "os/file.c",
            "iput_drop_only(receipt->ip)",
            "iput_drop_only_disabled(receipt->ip)",
        )
        self.assert_rejected("does not revalidate its inode classification")

    def test_drop_only_must_wake_cache_maintenance(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cache_retry_notify();",
            "(void)0;",
        )
        self.assert_rejected("does not wake blocked cache maintenance")

    def test_cache_retry_notification_must_publish_progress(self) -> None:
        self.mutate(
            "os/bio.c",
            "if (io_policy.background.cache_wait_pending) {\n"
            "\t\tbio_cache_advance_progress_locked();",
            "if (io_policy.background.cache_wait_pending) {\n"
            "\t\t(void)0;",
        )
        self.assert_rejected("does not publish progress")

    def test_cache_retry_notification_must_be_pending_only(self) -> None:
        self.mutate(
            "os/bio.c",
            "if (io_policy.background.cache_wait_pending) {",
            "if (1) {",
        )
        self.assert_rejected("without a pending background retry")

    def test_cache_retry_notification_cannot_wake_foreground(self) -> None:
        self.mutate(
            "os/bio.c",
            "\t\tbio_cache_advance_progress_locked();\n"
            "\t\twait_queue_wake_one(&background_cache_waiter);",
            "\t\tbio_cache_advance_progress_locked();\n"
            "\t\twait_queue_wake_all(&cache_waiters);",
        )
        self.assert_rejected("broadcast-wake foreground")

    def test_background_retry_wait_must_be_isolated(self) -> None:
        self.mutate(
            "os/bio.c",
            "wait_queue_sleep_irq_uninterruptible(\n"
            "\t\t\t    &background_cache_waiter)",
            "wait_queue_sleep_irq_uninterruptible(\n"
            "\t\t\t    &cache_waiters)",
        )
        self.assert_rejected("shares the foreground cache queue")

    def test_cleanup_token_must_begin_before_iput(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_begin(&receipt->cleanup_token)",
            "bio_cleanup_token_begin_disabled(&receipt->cleanup_token)",
        )
        self.assert_rejected("does not activate retained I/O ownership")

    def test_foreign_epoch_must_commit_before_cleanup_token(self) -> None:
        self.mutate(
            "os/file.c",
            "fs_epoch_prepare_cleanup_sponsor(\n"
            "\t\t    sponsor_owner, sponsor_class)",
            "0",
        )
        self.assert_rejected("cannot retire an incompatible epoch")

    def test_compatible_cleanup_epoch_must_match_owner(self) -> None:
        self.mutate(
            "os/fs_epoch.c",
            "compatible = dirty && epoch.owner == owner &&",
            "compatible = dirty && 1 &&",
        )
        self.assert_rejected("ignores the persistent owner")

    def test_compatible_cleanup_epoch_must_match_class(self) -> None:
        self.mutate(
            "os/fs_epoch.c",
            "epoch.sponsor_class == io_class &&",
            "1 &&",
        )
        self.assert_rejected("ignores the charged class")

    def test_compatible_cleanup_epoch_cannot_be_foreground(self) -> None:
        self.mutate(
            "os/fs_epoch.c",
            "epoch.sponsor_request_id == 0;",
            "1;",
        )
        self.assert_rejected("can reuse a foreground request")

    def test_destructive_close_must_not_flush_every_epoch(self) -> None:
        self.mutate(
            "os/file.c",
            "if (fs_epoch_should_commit() && fs_epoch_commit() < 0)",
            "if (fs_epoch_dirty() && fs_epoch_commit() < 0)",
        )
        self.assert_rejected("forces every reclaim epoch to flush")

    def test_cleanup_preview_and_begin_must_share_resolver(self) -> None:
        self.mutate(
            "os/bio.c",
            "\tif (bio_cleanup_resolve_sponsor_locked(\n"
            "\t\t    record, &effective_class, &execution_flag) < 0) {",
            "\tif (bio_cleanup_class_select(\n"
            "\t\t    record->owner, &effective_class) < 0) {",
        )
        self.assert_rejected("begin diverges from its prepared preview")

    def test_cleanup_token_cannot_capture_foreground_request(self) -> None:
        self.mutate(
            "os/bio.c",
            "\tuint retained_class;\n\tint enabled;",
            "\tstruct thread *thread = curr_thread();\n"
            "\tuint retained_class;\n\tint enabled;",
        )
        self.mutate(
            "os/bio.c",
            "\tif (bio_cleanup_class_select(owner, &retained_class) < 0) {",
            "\tif (thread->io_request_depth != 0)\n"
            "\t\tretained_class = thread->io_request_class;\n"
            "\telse if (bio_cleanup_class_select(owner, &retained_class) < 0) {",
        )
        self.assert_rejected("captures a foreground request identity")

    def test_cleanup_token_cannot_reuse_foreground_lease(self) -> None:
        self.mutate(
            "os/bio.c",
            "io_policy.deferred.reuse_request_lease = 0;",
            "io_policy.deferred.reuse_request_lease = 1;",
        )
        self.assert_rejected("publish through a foreground request lease")

    def test_cleanup_sponsor_coverage_must_match_class(self) -> None:
        self.mutate(
            "os/bio.c",
            "io_policy.deferred.io_class == io_class;",
            "1;",
        )
        self.assert_rejected("ignores the charged class")

    def test_cleanup_sponsor_coverage_must_match_owner(self) -> None:
        self.mutate(
            "os/bio.c",
            "io_policy.deferred.owner == owner &&",
            "1 &&",
        )
        self.assert_rejected("ignores the persistent owner")

    def test_cleanup_sponsor_coverage_cannot_reuse_foreground(self) -> None:
        self.mutate(
            "os/bio.c",
            "int covered = origin_request_id == 0 &&",
            "int covered = 1 &&",
        )
        self.assert_rejected("can reuse a foreground request")

    def test_epoch_commit_must_not_nest_inside_cleanup_token(self) -> None:
        self.mutate(
            "os/fs_epoch.c",
            "if (!bio_cleanup_sponsor_covers(\n"
            "\t\t    commit_owner, sponsor_class, sponsor_request_id)) {",
            "if (1) {",
        )
        self.assert_rejected("bypasses exact cleanup-sponsor matching")

    def test_cleanup_token_must_end_after_iput(self) -> None:
        self.mutate(
            "os/file.c",
            "bio_cleanup_token_end(&receipt->cleanup_token)",
            "bio_cleanup_token_end_disabled(&receipt->cleanup_token)",
        )
        self.assert_rejected("leaks active I/O ownership")

    def test_destructive_close_cannot_drain_owner_backlog(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (bio_cleanup_token_end(&receipt->cleanup_token) < 0)",
            "\t(void)fs_deferred_reclaim_drain_current();\n"
            "\tif (bio_cleanup_token_end(&receipt->cleanup_token) < 0)",
        )
        self.assert_rejected("synchronously drains an owner-wide reclaim backlog")

    def test_nested_reclaim_cannot_borrow_cleanup_token(self) -> None:
        self.mutate(
            "os/bio.c",
            "\tif (io_policy.deferred.active) {",
            "\tif (io_policy.deferred.active) {\n"
            "\t\tif (!bio_cleanup_handle_empty(&io_policy.deferred.token))\n"
            "\t\t\treturn 0;",
        )
        self.assert_rejected("unrelated cleanup-token class")

    def test_nested_reclaim_must_match_effective_class(self) -> None:
        self.mutate(
            "os/bio.c",
            "io_policy.deferred.io_class == io_class &&\n"
            "\t\t    io_policy.deferred.origin_request_id == origin_request_id",
            "io_policy.deferred.retained_class == io_class &&\n"
            "\t\t    io_policy.deferred.origin_request_id == origin_request_id",
        )
        self.assert_rejected("effective charged class")

    def test_deferred_end_cannot_consume_cleanup_token(self) -> None:
        self.mutate(
            "os/bio.c",
            "\tif (!bio_deferred_sponsor_current() ||\n"
            "\t    !bio_cleanup_handle_empty(&io_policy.deferred.token))",
            "\tif (!bio_deferred_sponsor_current())",
        )
        self.assert_rejected("consume a cleanup token")

    def test_settlement_must_stay_outside_fs_gate(self) -> None:
        self.mutate(
            "os/file.c",
            "\t    fs_epoch_request_held())",
            "\t    0)",
        )
        self.assert_rejected("settlement ignores gate ownership")

    def test_io_lease_must_end_before_gate(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\t\tsyscall_transaction_end_io(transaction);\n"
            "\t\tif (fs_epoch_request_begin() < 0)",
            "\t\tif (fs_epoch_request_begin() < 0)",
        )
        self.assert_rejected("violates FS-gate/BIO ordering")

    def test_gate_must_release_before_token_settlement(self) -> None:
        self.mutate(
            "os/syscall.c",
            "\tsyscall_transaction_end_io(transaction);\n"
            "\tif (final && receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT)",
            "\tif (final && receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT)",
        )
        self.assert_rejected("violates FS-gate/BIO ordering")

    def test_close_batch_must_be_single_token_bounded(self) -> None:
        self.mutate(
            "os/file.h",
            "#define FILE_CLOSE_BATCH_CAP 1U",
            "#define FILE_CLOSE_BATCH_CAP 2U",
        )
        self.assert_rejected("not incrementally bounded")

    def test_close_batch_must_transfer_before_consuming_receipt(self) -> None:
        self.mutate(
            "os/file.c",
            "\tbatch->pending[batch->count++] = receipt->cleanup_token;",
            "\tfileclose_receipt_complete(receipt);\n"
            "\tbatch->pending[batch->count++] = receipt->cleanup_token;",
        )
        self.assert_rejected("consume its transferred receipt exactly once")

    def test_close_batch_cannot_use_nested_wrapper(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (batch == 0 || f == 0 ||\n"
            "\t    batch->count > FILE_CLOSE_BATCH_CAP)",
            "\tfileclose(f);\n"
            "\tif (batch == 0 || f == 0 ||\n"
            "\t    batch->count > FILE_CLOSE_BATCH_CAP)",
        )
        self.assert_rejected("nested synchronous close wrapper")

    def test_close_batch_settlement_must_stay_outside_gate(self) -> None:
        self.mutate(
            "os/file.c",
            "batch->count > FILE_CLOSE_BATCH_CAP ||\n\t    fs_epoch_request_held()",
            "batch->count > FILE_CLOSE_BATCH_CAP ||\n\t    0",
        )
        self.assert_rejected("ignores filesystem-gate ownership")

    def test_close_batch_cannot_wait_on_prepare_inside_gate(self) -> None:
        self.mutate(
            "os/file.c",
            "\tif (prepared < 0)\n\t\treturn 1;",
            "\twhile (prepared < 0)\n"
            "\t\tprepared = fileclose_prepare(f, &receipt);",
        )
        self.assert_rejected("admission can wait")


if __name__ == "__main__":
    unittest.main()
