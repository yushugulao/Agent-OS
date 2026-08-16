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
    "agent_file_meta_set_batch",
    "agent_file_query",
    "agent_worker_create",
    "agent_task_channel_setup",
    "agent_task_channel_enter",
}

LIFECYCLE_CONTROL = {"exit", "agent_workflow_close"}

COMPLETION_SELF_GATED = {"wait4"}

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
    "agent_execution_contract",
    "agent_sched_config",
    "agent_audit_receipt",
    "agent_file_publish",
    "agent_task_channel_resource",
    "virtio_disk_test",
    "physical_page_test",
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
    if not COMPLETION_SELF_GATED <= exempt:
        raise ContractError(
            "self-gated completion wait entered outer cut: "
            f"{sorted(COMPLETION_SELF_GATED - exempt)}"
        )
    if "default:" not in exempt_text or not re.search(
        r"default\s*:\s*return\s+0\s*;", exempt_text
    ):
        raise ContractError("unknown syscalls do not fail closed as non-dispatched observers")

    dispatch = function_body(syscall_source, "syscall")
    gate = dispatch.find("workflow_lifecycle_operation_enter(lifecycle)")
    transaction = dispatch.find("ret = syscall_slow_path(")
    if min(gate, transaction) < 0 or gate >= transaction:
        raise ContractError("workflow cut no longer starts before transaction preparation")
    gate_complete = dispatch.find("operation_entered = 1;", gate)
    gate_denied = dispatch.find("operation_denied = 1;", gate)
    gate_abort = dispatch.find("goto operation_done;", gate_denied)
    if min(gate_denied, gate_abort, gate_complete) < 0 or not (
        gate < gate_denied < gate_abort < gate_complete
    ):
        raise ContractError("workflow admission can still run background after denial")
    file_pin = dispatch.find("&file_pin_guard", transaction)
    if file_pin < 0 or not (
        gate < gate_denied < gate_abort < gate_complete < transaction < file_pin
    ):
        raise ContractError("workflow cut no longer starts before transaction preparation")
    slow_path = function_body(syscall_source, "syscall_slow_path")
    pin_prepare = slow_path.find("syscall_transaction_prepare(")
    inode_only = slow_path.find("transaction->file->type == FD_INODE", pin_prepare)
    pin_enter = slow_path.find(
        "agent_execution_contract_file_pin_enter(", inode_only
    )
    direct_gate = slow_path.find(
        "agent_execution_contract_gate_direct_syscall(", pin_enter
    )
    if min(pin_prepare, inode_only, pin_enter, direct_gate) < 0 or not (
        pin_prepare < inode_only < pin_enter < direct_gate
    ):
        raise ContractError("descriptor publication cut is not inode-exact")
    operation_done = dispatch.find("operation_done:", transaction)
    denied_guard = dispatch.find("!operation_denied || direct_guard.active ||", operation_done)
    pin_guard = dispatch.find("file_pin_guard.active", denied_guard)
    checkpoint = dispatch.find("agent_background_checkpoint();", pin_guard)
    late_guard = dispatch.find("!background_done && !operation_denied", checkpoint)
    late_checkpoint = dispatch.find("agent_background_checkpoint();", late_guard)
    file_leave = dispatch.find(
        "agent_execution_contract_file_pin_leave(&file_pin_guard);", late_checkpoint
    )
    direct_leave = dispatch.find(
        "agent_execution_contract_direct_leave(&direct_guard);", file_leave
    )
    operation_leave_guard = dispatch.find("if (operation_entered)", direct_leave)
    operation_leave = dispatch.find(
        "workflow_lifecycle_operation_leave(lifecycle);", operation_leave_guard
    )
    background_leave_guard = dispatch.find(
        "if (background_operation_entered)", operation_leave
    )
    background_leave = dispatch.find(
        "workflow_lifecycle_operation_leave(lifecycle);", background_leave_guard
    )
    if min(
        operation_done,
        denied_guard,
        pin_guard,
        checkpoint,
        late_guard,
        late_checkpoint,
        file_leave,
        direct_leave,
        operation_leave_guard,
        operation_leave,
        background_leave_guard,
        background_leave,
    ) < 0 or not (
        operation_done
        < denied_guard
        < pin_guard
        < checkpoint
        < late_guard
        < late_checkpoint
        < file_leave
        < direct_leave
        < operation_leave_guard
        < operation_leave
        < background_leave_guard
        < background_leave
    ):
        raise ContractError("background checkpoint escaped the active contract guards")
    if dispatch.count("workflow_lifecycle_operation_leave(lifecycle);") != 2:
        raise ContractError("workflow cut does not have exactly two balanced leave sites")
    compact_dispatch = re.sub(r"\s+", "", dispatch)
    fence_guard = (
        "!(id==SYS_agent_run&&trapframe->a2==0&&"
        "trapframe->a3==AGENT_RUN_F_FENCE)"
    )
    if fence_guard not in compact_dispatch:
        raise ContractError("a completed workflow fence can run post-seal background work")

    wait = function_body(syscall_source, "sys_wait")
    wait_call = wait.find("child = wait(pid, &code);")
    gate = wait.find("workflow_lifecycle_operation_enter(lifecycle)")
    copyout = wait.find("copyout(p->pagetable, va, (char *)&code, sizeof(code))")
    leave = wait.find("workflow_lifecycle_operation_leave(lifecycle);")
    if min(wait_call, gate, copyout, leave) < 0 or not (
        wait_call < gate < copyout < leave
    ):
        raise ContractError("wait4 completion copyout is not self-gated after sleep")
    if "workflow_lifecycle_closing(lifecycle)" not in wait:
        raise ContractError("wait4 completion gate does not fail closed on lifecycle close")


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

    def mutate_cut(self, old: str, new: str) -> str:
        return self.mutate_function(
            "syscall_mutates_workflow_cut", old, new
        )

    def mutate_function(self, name: str, old: str, new: str) -> str:
        body = function_body(self.syscall, name)
        self.assertIn(old, body, f"{name} mutation anchor drifted: {old!r}")
        mutated_body = body.replace(old, new, 1)
        return self.syscall.replace(body, mutated_body, 1)

    def test_current_tree_passes(self) -> None:
        validate(self.syscall, self.registry)

    def test_rejects_missing_traditional_io_gate(self) -> None:
        old = "\t */\n\tcase SYS_write:\n\tcase SYS_read:"
        new = "\t */\n\tcase SYS_read:"
        self.assert_mutation_rejected(old, new, "registry is not closed")

    def test_rejects_self_gated_double_count(self) -> None:
        old = "\tcase SYS_agent_worker_create:\n"
        new = "\tcase SYS_agent_worker_create:\n\t\treturn 1;\n"
        mutated = self.mutate_cut(old, new)
        with self.assertRaisesRegex(ContractError, "one return"):
            validate(mutated, self.registry)

    def test_rejects_execution_contract_moved_inside_self_gate(self) -> None:
        outer = "\tcase SYS_agent_execution_contract:\n"
        self_gated_tail = (
            "\t\treturn 0;\n"
            "\t/* Exit/close drive their own lifecycle protocols; never nest the gate. */"
        )
        mutated = self.mutate_cut(outer, "")
        body = function_body(mutated, "syscall_mutates_workflow_cut")
        self.assertIn(self_gated_tail, body, "self-gated tail drifted")
        mutated_body = body.replace(
            self_gated_tail,
            "\tcase SYS_agent_execution_contract:\n"
            + self_gated_tail,
            1,
        )
        mutated = mutated.replace(body, mutated_body, 1)
        with self.assertRaisesRegex(ContractError, "ordinary workflow-cut set drifted"):
            validate(mutated, self.registry)

    def test_rejects_self_gated_task_channel_moved_to_outer_gate(self) -> None:
        outer_tail = "\tcase SYS_agent_route_config:\n\t\treturn 1;"
        self.assertIn(outer_tail, self.syscall, "outer cut anchor drifted")
        for syscall_name in (
            "agent_task_channel_setup",
            "agent_task_channel_enter",
        ):
            with self.subTest(syscall=syscall_name):
                self_gated = f"\tcase SYS_{syscall_name}:\n"
                mutated = self.mutate_cut(self_gated, "")
                body = function_body(mutated, "syscall_mutates_workflow_cut")
                self.assertIn(outer_tail, body, "outer cut anchor drifted")
                mutated_body = body.replace(
                    outer_tail,
                    f"\tcase SYS_{syscall_name}:\n" + outer_tail,
                    1,
                )
                mutated = mutated.replace(body, mutated_body, 1)
                with self.assertRaisesRegex(
                    ContractError, "ordinary workflow-cut set drifted"
                ):
                    validate(mutated, self.registry)

    def test_rejects_task_resource_removed_from_outer_gate(self) -> None:
        outer = "\tcase SYS_agent_task_channel_resource:\n"
        self_gated_tail = (
            "\tcase SYS_agent_task_channel_enter:\n"
            "\tcase SYS_agent_task_delegate_claim:\n"
            "\tcase SYS_agent_task_delegate_complete:\n"
            "\t\treturn 0;"
        )
        mutated = self.mutate_cut(outer, "")
        body = function_body(mutated, "syscall_mutates_workflow_cut")
        self.assertIn(self_gated_tail, body, "Task self-gated tail drifted")
        mutated_body = body.replace(
            self_gated_tail,
            self_gated_tail.replace(
                "\t\treturn 0;",
                "\tcase SYS_agent_task_channel_resource:\n\t\treturn 0;",
            ),
            1,
        )
        mutated = mutated.replace(body, mutated_body, 1)
        with self.assertRaisesRegex(
            ContractError, "ordinary workflow-cut set drifted"
        ):
            validate(mutated, self.registry)

    def test_rejects_completion_wait_outer_gate(self) -> None:
        observers = "\tcase SYS_wait4:\n\t\treturn 0;"
        outer_tail = "\tcase SYS_agent_route_config:\n\t\treturn 1;"
        self.assertIn(observers, self.syscall, "completion wait anchor drifted")
        self.assertIn(outer_tail, self.syscall, "outer cut anchor drifted")
        mutated = self.syscall.replace(observers, "", 1).replace(
            outer_tail,
            "\tcase SYS_agent_route_config:\n"
            "\tcase SYS_wait4:\n"
            "\t\treturn 1;",
            1,
        )
        with self.assertRaisesRegex(ContractError, "ordinary workflow-cut set drifted"):
            validate(mutated, self.registry)

    def test_rejects_wait4_copyout_without_inner_gate(self) -> None:
        self.assert_mutation_rejected(
            "\t\tworkflow_lifecycle_operation_leave(lifecycle);\n\treturn result;",
            "\treturn result;",
            "completion copyout is not self-gated",
        )

    def test_rejects_gate_after_transaction(self) -> None:
        old = "if (workflow_lifecycle_operation_enter(lifecycle) < 0) {"
        new = "if (workflow_lifecycle_operation_enter_after(lifecycle) < 0) {"
        mutated = self.mutate_function("syscall", old, new)
        with self.assertRaisesRegex(ContractError, "starts before transaction"):
            validate(mutated, self.registry)

    def test_rejects_background_after_leave(self) -> None:
        old = (
            "\tagent_execution_contract_file_pin_leave(&file_pin_guard);\n"
            "\tif (direct_guard.active)\n"
            "\t\tagent_execution_contract_direct_leave(&direct_guard);\n"
            "\tif (operation_entered)\n"
            "\t\tworkflow_lifecycle_operation_leave(lifecycle);"
        )
        new = (
            "\tif (operation_entered)\n"
            "\t\tworkflow_lifecycle_operation_leave(lifecycle);\n"
            "\tagent_execution_contract_file_pin_leave(&file_pin_guard);\n"
            "\tif (direct_guard.active)\n"
            "\t\tagent_execution_contract_direct_leave(&direct_guard);"
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
