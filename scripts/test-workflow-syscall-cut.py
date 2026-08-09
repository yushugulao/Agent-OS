#!/usr/bin/env python3
"""Static mutation tests for the ordinary-syscall workflow fence cut."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSCALL = ROOT / "os" / "syscall.c"
REGISTRY = ROOT / "os" / "syscall_counter.h"

SELF_GATED = {
    "clone",
    "agent_create",
    "agent_create_role",
    "agent_workflow_create",
    "agent_run",
    "agent_call",
    "tool_call",
    "agent_file_meta_init",
    "agent_file_meta_set",
    "agent_file_query",
    "agent_worker_create",
}

LIFECYCLE_CONTROL = {"exit", "agent_workflow_close"}

REQUIRED_OUTER = {
    "write",
    "read",
    "fstat",
    "openat",
    "unlinkat",
    "close",
    "sync",
    "fsync",
    "fdatasync",
    "brk",
    "mailread",
    "mailwrite",
    "trace",
    "execve",
    "wait4",
    "pipe2",
    "thread_create",
    "waittid",
    "mutex_create",
    "mutex_lock",
    "mutex_unlock",
    "semaphore_create",
    "semaphore_up",
    "semaphore_down",
    "condvar_create",
    "condvar_signal",
    "condvar_wait",
    "agent_scope_delegate_fd",
    "agent_sched_config",
    "agent_audit_receipt",
    "agent_observe_recovery",
    "virtio_disk_test",
    "physical_page_test",
    "agent_metadata_test",
    "wait_atomic_test",
    "fs_allocator_fault_test",
    "context_push",
    "context_rollback",
    "context_clear",
    "agent_watch",
    "agent_unwatch",
    "agent_wait",
    "agent_wait_cancel",
    "agent_heartbeat",
    "agent_heartbeat_set",
    "agent_heartbeat_stop",
    "agent_wake",
    "agent_file_edit_begin",
    "agent_file_edit_commit",
    "agent_file_edit_abort",
    "agent_file_edit_state",
    "agent_route_config",
}


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function {name}")
    start = source.find("{", match.start())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise ContractError(f"unterminated function {name}")


def registry_names(header: str) -> tuple[set[str], set[str]]:
    registered_match = re.search(
        r"#define\s+SYSCALL_REGISTERED\(X\)\s*\\(?P<body>.*?)\n\n",
        header,
        re.S,
    )
    aliases_match = re.search(
        r"#define\s+SYSCALL_ALIASES\(X\)\s*\\(?P<body>.*?)\n\n",
        header,
        re.S,
    )
    if registered_match is None or aliases_match is None:
        raise ContractError("syscall registry macros are missing")
    registered = set(
        re.findall(r"\bX\(\s*([A-Za-z0-9_]+)\s*,", registered_match["body"])
    )
    aliases = set(
        re.findall(r"\bX\(\s*([A-Za-z0-9_]+)\s*,", aliases_match["body"])
    )
    if not registered or not aliases:
        raise ContractError("syscall registry parsing produced an empty set")
    return registered, aliases


def validate(syscall_source: str, registry_source: str) -> None:
    registered, aliases = registry_names(registry_source)
    classified = registered | aliases
    body = function_body(syscall_source, "syscall_mutates_workflow_cut")
    cases = re.findall(r"\bcase\s+SYS_([A-Za-z0-9_]+)\s*:", body)
    counts = {name: cases.count(name) for name in set(cases)}
    duplicate = sorted(name for name, count in counts.items() if count != 1)
    if duplicate:
        raise ContractError(f"duplicate workflow-cut classifications: {duplicate}")
    missing = sorted(classified - set(cases))
    extra = sorted(set(cases) - classified)
    if missing or extra:
        raise ContractError(
            f"workflow-cut registry is not closed: missing={missing}, extra={extra}"
        )
    if body.count("return 1;") != 1:
        raise ContractError("outer workflow-cut classification must have one return")
    outer_text, exempt_text = body.split("return 1;", 1)
    outer = set(re.findall(r"\bcase\s+SYS_([A-Za-z0-9_]+)\s*:", outer_text))
    exempt = set(re.findall(r"\bcase\s+SYS_([A-Za-z0-9_]+)\s*:", exempt_text))
    if outer != REQUIRED_OUTER:
        raise ContractError(
            "ordinary workflow-cut set drifted: "
            f"missing={sorted(REQUIRED_OUTER - outer)}, "
            f"unexpected={sorted(outer - REQUIRED_OUTER)}"
        )
    if not SELF_GATED <= exempt:
        raise ContractError(f"self-gated syscalls entered outer cut: {sorted(SELF_GATED - exempt)}")
    if not LIFECYCLE_CONTROL <= exempt:
        raise ContractError(
            "lifecycle control entered outer cut: "
            f"{sorted(LIFECYCLE_CONTROL - exempt)}"
        )
    if "default:" not in exempt_text or not re.search(
        r"default\s*:\s*return\s+0\s*;", exempt_text
    ):
        raise ContractError("unknown syscalls do not fail closed as non-dispatched observers")

    dispatch = function_body(syscall_source, "syscall")
    gate = dispatch.find("workflow_lifecycle_operation_enter(lifecycle)")
    transaction = dispatch.find("syscall_slow_path(trapframe, id, policy)")
    if gate < 0 or transaction < 0 or gate > transaction:
        raise ContractError("workflow cut no longer starts before transaction preparation")
    if "operation_denied = 1;" not in dispatch:
        raise ContractError("failed workflow-cut admission can still run background work")
    operation_block = re.search(
        r"if\s*\(operation_entered\)\s*\{(?P<body>.*?)\n\s*\}", dispatch, re.S
    )
    if operation_block is None:
        raise ContractError("missing common workflow-cut completion block")
    completion = operation_block["body"]
    checkpoint = completion.find("agent_background_checkpoint();")
    leave = completion.find("workflow_lifecycle_operation_leave(lifecycle);")
    if checkpoint < 0 or leave < 0 or checkpoint > leave:
        raise ContractError("background checkpoint escaped the active workflow cut")
    if dispatch.count("workflow_lifecycle_operation_leave(lifecycle);") != 2:
        raise ContractError("workflow cut does not have exactly two balanced leave sites")
    compact_dispatch = re.sub(r"\s+", "", dispatch)
    fence_guard = (
        "!(id==SYS_agent_run&&trapframe->a2==0&&"
        "trapframe->a3==AGENT_RUN_F_FENCE)"
    )
    if fence_guard not in compact_dispatch:
        raise ContractError("a completed workflow fence can run post-seal background work")


class WorkflowSyscallCutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.syscall = SYSCALL.read_text(encoding="utf-8")
        cls.registry = REGISTRY.read_text(encoding="utf-8")

    def assert_mutation_rejected(self, old: str, new: str, message: str) -> None:
        self.assertIn(old, self.syscall, f"mutation anchor drifted: {old!r}")
        mutated = self.syscall.replace(old, new, 1)
        with self.assertRaisesRegex(ContractError, message):
            validate(mutated, self.registry)

    def test_current_tree_passes(self) -> None:
        validate(self.syscall, self.registry)

    def test_rejects_missing_traditional_io_gate(self) -> None:
        old = "\t */\n\tcase SYS_write:\n\tcase SYS_read:"
        new = "\t */\n\tcase SYS_read:"
        self.assert_mutation_rejected(old, new, "registry is not closed")

    def test_rejects_self_gated_double_count(self) -> None:
        old = "\tcase SYS_agent_worker_create:\n\t\treturn 0;"
        new = "\tcase SYS_agent_worker_create:\n\t\treturn 1;"
        self.assert_mutation_rejected(old, new, "one return")

    def test_rejects_gate_after_transaction(self) -> None:
        old = "if (workflow_lifecycle_operation_enter(lifecycle) < 0) {"
        new = "if (workflow_lifecycle_operation_enter_after(lifecycle) < 0) {"
        self.assert_mutation_rejected(old, new, "starts before transaction")

    def test_rejects_background_after_leave(self) -> None:
        old = (
            "\t\t\tif (agent_background_work_pending() || id == SYS_sched_yield)\n"
            "\t\t\t\tagent_background_checkpoint();\n"
            "\t\t\tbackground_done = 1;\n"
            "\t\t\tworkflow_lifecycle_operation_leave(lifecycle);"
        )
        new = (
            "\t\t\tbackground_done = 1;\n"
            "\t\t\tworkflow_lifecycle_operation_leave(lifecycle);\n"
            "\t\t\tif (agent_background_work_pending() || id == SYS_sched_yield)\n"
            "\t\t\t\tagent_background_checkpoint();"
        )
        self.assert_mutation_rejected(old, new, "checkpoint escaped")

    def test_rejects_failed_gate_background_fallthrough(self) -> None:
        self.assert_mutation_rejected(
            "\t\t\t\toperation_denied = 1;\n",
            "",
            "admission can still run background",
        )

    def test_rejects_post_seal_background_checkpoint(self) -> None:
        old = (
            "\t    !(id == SYS_agent_run && trapframe->a2 == 0 &&\n"
            "\t      trapframe->a3 == AGENT_RUN_F_FENCE) &&\n"
        )
        self.assert_mutation_rejected(
            old,
            "",
            "post-seal background work",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
