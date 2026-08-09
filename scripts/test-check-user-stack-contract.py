#!/usr/bin/env python3
"""共享 exec argv 与用户栈契约的变异测试。"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER = Path(__file__).with_name("check-user-stack-contract.py")
REPO = Path(__file__).resolve().parent.parent
FILES = (
    "user_stack_policy.h",
    "os/user_stack_layout.h",
    "os/loader.h",
    "os/proc.c",
    "os/syscall.c",
    "user/Makefile",
    "user/lib/research_platform_state.c",
    "user/include/user_stack_policy.h",
    "user/src/usersafety_ucore.c",
    "scripts/run-agent-tests.sh",
    "scripts/validate-kernel-test-log.py",
    "scripts/test-check-user-stack-contract.py",
    "Makefile",
)


class UserStackContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)

    def tearDown(self):
        self.temporary.cleanup()

    def mutate(self, relative, old, new):
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"stale mutation fixture: {relative}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_rejected(self, needle):
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_accepts_complete_contract(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("boundary=1024 overflow=1040", result.stdout)

    def test_mutation_policy_constant_drift_is_rejected(self):
        self.mutate(
            "user_stack_policy.h",
            "#define USER_STACK_ARGV_LAYOUT_BYTES 1024ULL",
            "#define USER_STACK_ARGV_LAYOUT_BYTES 1040ULL",
        )
        self.assert_rejected("constant drift")

    def test_mutation_loader_boundary_bypass_is_rejected(self):
        self.mutate(
            "os/proc.c",
            "user_stack_argv_layout_add_string(&layout, n)",
            "user_stack_argv_layout_add_string_bypassed(&layout, n)",
        )
        self.assert_rejected("loader string accounting")

    def test_mutation_user_policy_wrapper_bypass_is_rejected(self):
        self.mutate(
            "user/include/user_stack_policy.h",
            '#include "../../user_stack_policy.h"',
            "#define USER_STACK_ARGV_LAYOUT_BYTES 4096",
        )
        self.assert_rejected("user policy wrapper include")

    def test_mutation_syscall_boundary_bypass_is_rejected(self):
        self.mutate(
            "os/syscall.c",
            "USER_STACK_ARGV_LAYOUT_BYTES - storage_used",
            "USTACK_SIZE - storage_used",
        )
        self.assert_rejected("syscall bounded argument copy")

    def test_mutation_pointer_vector_omission_is_rejected(self):
        self.mutate(
            "os/user_stack_layout.h",
            "pointer_bytes = (layout->argc + 1) * USER_STACK_POINTER_BYTES;",
            "pointer_bytes = 0;",
        )
        self.assert_rejected("argv pointer-vector accounting")

    def test_mutation_per_string_alignment_omission_is_rejected(self):
        self.mutate(
            "os/user_stack_layout.h",
            "next = user_stack_layout_align(layout->used + bytes);",
            "next = layout->used + bytes;",
        )
        self.assert_rejected("per-string alignment accounting")

    def test_mutation_preflight_after_write_is_rejected(self):
        self.mutate(
            "os/proc.c",
            "if (user_range_check(pagetable, argv_sp, layout_bytes, PTE_W) < 0)\n\t\treturn -1;",
            "/* range preflight removed */",
        )
        self.assert_rejected("stack range preflight")

    def test_mutation_liveness_evidence_removal_is_rejected(self):
        self.mutate(
            "user/src/usersafety_ucore.c",
            'check_live("exec argv layout budget");',
            "/* liveness proof removed */",
        )
        self.assert_rejected("reject-and-live")

    def test_mutation_runner_marker_removal_is_rejected(self):
        self.mutate(
            "scripts/run-agent-tests.sh",
            "usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 over_limit_rejected=1 caller_live=1",
            "usersafety_ucore: argv layout omitted",
        )
        self.assert_rejected("runner exact argv marker")

    def test_mutation_validator_marker_removal_is_rejected(self):
        self.mutate(
            "scripts/validate-kernel-test-log.py",
            "usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 over_limit_rejected=1 caller_live=1",
            "usersafety_ucore: argv layout omitted",
        )
        self.assert_rejected("validator exact argv marker")

    def test_mutation_selftest_wiring_removal_is_rejected(self):
        self.mutate(
            "Makefile",
            "scripts/test-check-user-stack-contract.py",
            "scripts/test-check-user-stack-contract-removed.py",
        )
        self.assert_rejected("mutation test wiring")

    def test_mutation_compatibility_benchmark_omission_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(filter-out $(STACK_USAGE_SUPPORT_SRCS),$(addprefix user/,$(sort $(SRCS)))) \\\n\t$(COMPAT_BENCH_REPO_SOURCE)",
            "$(filter-out $(STACK_USAGE_SUPPORT_SRCS),$(addprefix user/,$(sort $(SRCS))))",
        )
        self.assert_rejected("complete stack application inventory")

    def test_mutation_worker_support_inventory_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "STACK_USAGE_SUPPORT_SRCS := $(addprefix user/src/,$(addsuffix .c,$(WORKER_BATCH_PROGRAMS)))",
            "STACK_USAGE_SUPPORT_SRCS :=",
        )
        self.assert_rejected("complete worker support stack inventory")

    def test_mutation_compatibility_benchmark_source_drift_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "COMPAT_BENCH_REPO_SOURCE := evaluation_guest/compatbench.c",
            "COMPAT_BENCH_REPO_SOURCE := user/src/agentbench_ucore.c",
        )
        self.assert_rejected("canonical compatibility benchmark source")

    def test_mutation_stack_library_inventory_shrink_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(addprefix user/,$(sort $(LIB_C)))",
            "$(addprefix user/,$(firstword $(sort $(LIB_C))))",
        )
        self.assert_rejected("complete stack library inventory")

    def test_mutation_data_only_partition_removal_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "STACK_USAGE_DATA_ONLY_LIBRARY_SRCS := user/lib/research_platform_state.c",
            "STACK_USAGE_DATA_ONLY_LIBRARY_SRCS :=",
        )
        self.assert_rejected("data-only stack library inventory")

    def test_mutation_data_only_filter_bypass_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(filter-out $(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS),$(STACK_USAGE_ALL_LIBRARY_SRCS))",
            "$(STACK_USAGE_ALL_LIBRARY_SRCS)",
        )
        self.assert_rejected("function/data-only stack inventory partition")

    def test_mutation_data_only_function_is_rejected(self):
        self.mutate(
            "user/lib/research_platform_state.c",
            "int rp_host_seed_loaded;",
            "int rp_host_seed_loaded;\nint forbidden_function(void) { return 0; }",
        )
        self.assert_rejected("contains a function definition")

    def test_mutation_data_only_object_gate_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "grep -Eq '[[:space:]]F[[:space:]]'",
            "grep -Eq '[[:space:]]O[[:space:]]'",
        )
        self.assert_rejected("object function-symbol rejection")

    def test_mutation_stack_application_inventory_shrink_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(addprefix user/,$(sort $(SRCS)))",
            "$(addprefix user/,$(firstword $(sort $(SRCS))))",
        )
        self.assert_rejected("complete stack application inventory")

    def test_mutation_compatibility_benchmark_stack_rule_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(STACK_USAGE_DIR)/evaluation_guest/compatbench.o:",
            "$(STACK_USAGE_DIR)/evaluation_guest/compatbench-omitted.o:",
        )
        self.assert_rejected("shared compatibility benchmark stack build")

    def test_mutation_compatibility_benchmark_compile_source_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "-c $(COMPAT_BENCH_REPO_SOURCE) \\",
            "-c user/src/agentbench_ucore.c \\",
        )
        self.assert_rejected("canonical compatibility benchmark stack compilation")

    def test_mutation_compatibility_benchmark_link_source_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "$(COMPAT_BENCH_SOURCE) -o $@",
            "../user/src/agentbench_ucore.c -o $@",
        )
        self.assert_rejected("canonical compatibility benchmark link")

    def test_mutation_generated_header_stack_path_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "-I$(abspath $(generated_dir))",
            "-I$(generated_dir)",
        )
        self.assert_rejected("absolute generated-header stack include")

    def test_mutation_stack_compiler_flag_parity_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "STACK_USAGE_CFLAGS := $(COMMON_CFLAGS)",
            "STACK_USAGE_CFLAGS := -Os",
        )
        self.assert_rejected("stack compiler flag parity")

    def test_mutation_application_compiler_flag_parity_is_rejected(self):
        self.mutate(
            "user/Makefile",
            "CFLAGS := $(COMMON_CFLAGS)",
            "CFLAGS := -Os",
        )
        self.assert_rejected("canonical application compiler flags")

    def test_mutation_repository_source_root_is_rejected(self):
        self.mutate(
            "user/Makefile",
            '--usage-dir "$$scratch/usage" --source-dir ..',
            '--usage-dir "$$scratch/usage" --source-dir .',
        )
        self.assert_rejected("repository-rooted stack source inventory")


if __name__ == "__main__":
    unittest.main()
