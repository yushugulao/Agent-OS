#!/usr/bin/env python3
"""Mutation tests for attributable syscall work receipts."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-kernel-work-receipt.py"
FILES = (
    "kernel_work_abi.h",
    "os/kernel_work.h",
    "os/kernel_work.c",
    "os/proc.h",
    "os/trap.c",
    "os/syscall.c",
    "os/syscall_ids.h",
    "user/include/unistd.h",
    "user/lib/syscall.c",
    "user/lib/syscall_ids.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/src/agentfs_ucore.c",
)


class KernelWorkReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self):
        self.temporary.cleanup()

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def mutate(self, relative, old, new):
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self, needle):
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_current_tree_passes(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_begin_ownership_starts_only_at_depth_zero(self):
        self.mutate(
            "os/kernel_work.c",
            "if (t->kernel_work_depth == 0) {",
            "if (1) {",
        )
        self.assert_rejected("outermost begin")

    def test_only_outer_completion_may_publish(self):
        self.mutate(
            "os/kernel_work.c",
            "if (terminal || outer) {\n\t\t(void)kernel_work_checkpoint_mode",
            "if (1) {\n\t\t(void)kernel_work_checkpoint_mode",
        )
        self.assert_rejected("limited to outer completion")

    def test_publish_requires_captured_owner(self):
        self.mutate(
            "os/kernel_work.c",
            "if (t->kernel_work_publish_receipt)\n\t\t\tkernel_work_publish_receipt(t);",
            "if (1)\n\t\t\tkernel_work_publish_receipt(t);",
        )
        self.assert_rejected("limited to outer completion")

    def test_terminal_completion_resets_depth(self):
        self.mutate(
            "os/kernel_work.c",
            "if (terminal)\n\t\tt->kernel_work_depth = 0;",
            "if (terminal)\n\t\tt->kernel_work_depth--;",
        )
        self.assert_rejected("terminal completion")

    def test_snapshot_uses_published_target_not_live_scope(self):
        self.mutate(
            "os/kernel_work.c",
            "receipt->syscall_id = t->kernel_receipt_syscall_id;",
            "receipt->syscall_id = t->kernel_work_target_syscall_id;",
        )
        self.assert_rejected("immutable receipt")

    def test_legacy_getter_uses_published_receipt(self):
        self.mutate(
            "os/syscall.c",
            "return kernel_work_last_preemptions(curr_thread());",
            "return curr_thread()->kernel_work_redispatches;",
        )
        self.assert_rejected("legacy getter")

    def test_snapshot_observer_cannot_publish(self):
        self.mutate(
            "os/syscall.c",
            "case SYS_kernel_work_receipt_snapshot:\n\t\treturn KERNEL_WORK_SYSCALL_OBSERVER;",
            "case SYS_kernel_work_receipt_snapshot:\n\t\treturn KERNEL_WORK_SYSCALL_PUBLISH;",
        )
        self.assert_rejected("non-publishing syscall class")

    def test_timer_scope_cannot_publish(self):
        self.mutate(
            "os/kernel_work.c",
            "void kernel_work_begin_background(void)\n{\n\tkernel_work_begin_scope(-1, 0);",
            "void kernel_work_begin_background(void)\n{\n\tkernel_work_begin_scope(-1, 1);",
        )
        self.assert_rejected("background begin")

    def test_timer_trap_cannot_enter_syscall_publisher(self):
        self.mutate(
            "os/trap.c",
            "kernel_work_begin_background();",
            "kernel_work_begin_syscall(0, KERNEL_WORK_SYSCALL_PUBLISH);",
        )
        self.assert_rejected("timer maintenance")

    def test_timer_cannot_directly_overwrite_receipt_storage(self):
        self.mutate(
            "os/trap.c",
            "kernel_work_end_background();",
            "kernel_work_end_background();\n"
            "\t\tcurr_thread()->kernel_last_syscall_preemptions = 0;",
        )
        self.assert_rejected("outside the kernel work canonical owner")

    def test_timer_epoch_must_advance_in_timer_trap(self):
        self.mutate(
            "os/trap.c", "kernel_work_timer_advance();", "kernel_work_request_resched();"
        )
        self.assert_rejected("trusted epoch")

    def test_generation_must_come_from_global_monotonic_source(self):
        self.mutate(
            "os/kernel_work.c",
            "t->kernel_receipt_generation = kernel_work_receipt_next_generation;",
            "t->kernel_receipt_generation++;",
        )
        self.assert_rejected("atomically sourced")

    def test_user_workload_cannot_depend_on_rdtime(self):
        self.mutate(
            "user/src/agentfs_ucore.c",
            "static void observe_query_receipt(int owner_tid, int owner_pid)",
            "static const char *forbidden_rdtime = \"rdtime\";\n"
            "static void observe_query_receipt(int owner_tid, int owner_pid)",
        )
        self.assert_rejected("user-mode rdtime")

    def test_syscall_ids_must_match(self):
        self.mutate(
            "user/lib/syscall_ids.h",
            "#define SYS_kernel_work_receipt_snapshot 558",
            "#define SYS_kernel_work_receipt_snapshot 559",
        )
        self.assert_rejected("IDs diverge")


if __name__ == "__main__":
    unittest.main()
