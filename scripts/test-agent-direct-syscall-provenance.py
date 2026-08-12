#!/usr/bin/env python3
"""Static regressions for direct-effect classification and ingress tainting."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSCALL = ROOT / "os" / "syscall.c"
IPC = ROOT / "os" / "agent_ipc.c"


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?m)^(?:static\s+)?"
        rf"(?:__attribute__\s*\(\([^)]*\)\)\s+)?"
        rf"(?:int|uint|uint64|void)\s*(?:\n\s*)?"
        rf"{re.escape(name)}\s*\([^;{{}}]*?\)\s*\{{",
        source,
        re.S,
    )
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


def ordered(body: str, *needles: str) -> None:
    cursor = -1
    for needle in needles:
        position = body.find(needle, cursor + 1)
        if position < 0:
            raise ContractError(f"missing ordered token: {needle}")
        cursor = position


def validate(syscall: str, ipc: str) -> None:
    prepare = function_body(syscall, "syscall_transaction_prepare")
    for syscall_name in ("SYS_read", "SYS_write"):
        if syscall_name not in prepare:
            raise ContractError(f"{syscall_name} no longer pins its descriptor")
    if "SYS_close" in prepare:
        raise ContractError("SYS_close must not guess an identity before detach")
    if "transaction->file = syscall_fd_pin(trapframe->a0);" not in prepare:
        raise ContractError("descriptor pin is not captured in the transaction")

    effects = function_body(syscall, "syscall_direct_agent_side_effects")
    required = (
        "case SYS_write:",
        "transaction != 0 ? transaction->file : 0",
        "case SYS_openat:",
        "trapframe->a1 & (O_CREATE | O_TRUNC)",
        "case SYS_close:",
        "case SYS_agent_workflow_close:",
        "AGENT_SIDE_EFFECT_PROCESS | AGENT_SIDE_EFFECT_PERMISSION",
    )
    for token in required:
        if token not in effects:
            raise ContractError(f"direct-effect classifier lost {token}")
    close_start = effects.index("case SYS_close:")
    close_end = effects.index("case SYS_unlinkat:", close_start)
    close_effects = effects[close_start:close_end]
    for token in (
        "AGENT_SIDE_EFFECT_FILE",
        "AGENT_SIDE_EFFECT_METADATA",
        "AGENT_SIDE_EFFECT_IPC",
    ):
        if token not in close_effects:
            raise ContractError(f"close conservative effect union lost {token}")
    if "transaction->file" in close_effects:
        raise ContractError("close classifier reused a speculative descriptor pin")

    publish_match = re.search(
        r"case SYS_agent_file_publish:(.*?)(?:case SYS_|default:)",
        effects,
        re.S,
    )
    if publish_match is None:
        raise ContractError("atomic publish lacks a direct-effect classification")
    publish_effects = set(
        re.findall(r"AGENT_SIDE_EFFECT_[A-Z]+", publish_match.group(1))
    )
    expected_publish_effects = {
        "AGENT_SIDE_EFFECT_FILE",
        "AGENT_SIDE_EFFECT_METADATA",
        "AGENT_SIDE_EFFECT_ARTIFACT",
    }
    if publish_effects != expected_publish_effects:
        raise ContractError(
            f"atomic publish effect union drifted: {sorted(publish_effects)}"
        )

    file_effects = function_body(syscall, "syscall_file_side_effects")
    for token in (
        "case FD_STDIO:",
        "return 0;",
        "case FD_PIPE:",
        "return AGENT_SIDE_EFFECT_IPC;",
        "case FD_INODE:",
        "AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_ARTIFACT",
    ):
        if token not in file_effects:
            raise ContractError(f"file classifier lost {token}")
    if "fdtable[" in file_effects:
        raise ContractError("file effects must use the pinned object, not an fd number")

    ingress = function_body(syscall, "syscall_merge_ingress_provenance")
    for token in (
        "case FD_STDIO:",
        "AGENT_PROVENANCE_TRUSTED_USER_CONTROL",
        "case FD_PIPE:",
        "AGENT_PROVENANCE_CROSS_AGENT_DATA",
        "AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT",
        "case FD_INODE:",
        "AGENT_PROVENANCE_UNTRUSTED_FILE_DATA",
        "case SYS_mailread:",
        "agent_provenance_merge_current(p, labels)",
    ):
        if token not in ingress:
            raise ContractError(f"ingress classifier lost {token}")

    slow = function_body(syscall, "syscall_slow_path")
    ordered(
        slow,
        "syscall_transaction_prepare(transaction, trapframe, id, policy);",
        "agent_execution_contract_gate_direct_syscall(",
        "syscall_merge_ingress_provenance(id, transaction)",
        "syscall_transaction_begin(transaction, trapframe)",
        "syscall_dispatch(id, trapframe, transaction)",
        "syscall_transaction_finish(transaction, &ret);",
    )

    fstat = function_body(syscall, "sys_fstat")
    ordered(
        fstat,
        "agent_provenance_merge_current(",
        "copyout(p->pagetable, stataddr",
    )

    wait = function_body(ipc, "sys_agent_wait")
    ordered(
        wait,
        "agent_provenance_merge_current(",
        "copyout(p->pagetable, eventaddr",
    )

    wait4 = function_body(syscall, "sys_wait")
    if "child < 0 || va == 0" in wait4:
        raise ContractError("wait4 exposes a consumed child before ingress taint")
    if wait4.count("agent_provenance_merge_current(") != 1:
        raise ContractError("wait4 must merge child ingress exactly once")
    for token in (
        "AGENT_PROVENANCE_CROSS_AGENT_DATA",
        "AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT",
    ):
        if token not in wait4:
            raise ContractError(f"wait4 ingress lost {token}")
    ordered(
        wait4,
        "child = wait(pid, &code);",
        "lifecycle = vfs_proc_lifecycle(p);",
        "workflow_lifecycle_operation_enter(lifecycle)",
        "agent_provenance_merge_current(",
        "else if (va == 0)",
        "copyout(p->pagetable, va, (char *)&code, sizeof(code))",
        "workflow_lifecycle_operation_leave(lifecycle);",
        "return result;",
    )


class DirectSyscallProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.syscall = SYSCALL.read_text(encoding="utf-8")
        cls.ipc = IPC.read_text(encoding="utf-8")

    def assert_syscall_mutation_rejected(self, old: str, new: str) -> None:
        self.assertIn(old, self.syscall)
        with self.assertRaises(ContractError):
            validate(self.syscall.replace(old, new, 1), self.ipc)

    def test_current_tree_passes(self) -> None:
        validate(self.syscall, self.ipc)

    def test_rejects_fd_number_stdio_shortcut(self) -> None:
        self.assert_syscall_mutation_rejected(
            "case FD_STDIO:\n\t\treturn 0;",
            "case FD_STDIO:\n\t\treturn file == curr_proc()->fdtable[1] ? 0 : "
            "AGENT_SIDE_EFFECT_ALL;",
        )

    def test_rejects_write_gate_before_descriptor_pin(self) -> None:
        self.assert_syscall_mutation_rejected(
            "syscall_transaction_prepare(transaction, trapframe, id, policy);",
            "/* descriptor preparation removed */",
        )

    def test_rejects_close_pre_detach_identity_guess(self) -> None:
        self.assert_syscall_mutation_rejected(
            "case SYS_read:\n\tcase SYS_write:",
            "case SYS_read:\n\tcase SYS_write:\n\tcase SYS_close:",
        )

    def test_rejects_close_effect_underclassification(self) -> None:
        self.assert_syscall_mutation_rejected(
            "return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA |\n"
            "\t\t       AGENT_SIDE_EFFECT_IPC;",
            "return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA;",
        )

    def test_rejects_atomic_publish_effect_underclassification(self) -> None:
        self.assert_syscall_mutation_rejected(
            "return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA |\n"
            "\t\t       AGENT_SIDE_EFFECT_ARTIFACT;",
            "return AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA;",
        )

    def test_rejects_ingress_merge_after_dispatch(self) -> None:
        old = (
            "\tif (syscall_merge_ingress_provenance(id, transaction) < 0) {\n"
            "\t\t*operation_denied = 1;\n"
            "\t\tgoto finish;\n"
            "\t}\n"
        )
        self.assert_syscall_mutation_rejected(old, "")

    def test_rejects_read_file_taint_removal(self) -> None:
        self.assert_syscall_mutation_rejected(
            "labels = AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;",
            "labels = 0;",
        )

    def test_rejects_agent_wait_copyout_before_taint(self) -> None:
        merge = self.ipc.find("agent_provenance_merge_current(")
        copyout = self.ipc.find("copyout(p->pagetable, eventaddr", merge)
        self.assertGreaterEqual(merge, 0)
        self.assertGreater(copyout, merge)
        mutated = self.ipc[:merge] + self.ipc[copyout:] + self.ipc[merge:copyout]
        with self.assertRaises(ContractError):
            validate(self.syscall, mutated)

    def test_rejects_wait4_unlabelled_child_ingress(self) -> None:
        self.assert_syscall_mutation_rejected(
            "p, AGENT_PROVENANCE_CROSS_AGENT_DATA |\n"
            "\t\t\t       AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT",
            "p, AGENT_PROVENANCE_TRUSTED_USER_CONTROL",
        )

    def test_rejects_wait4_va_zero_early_return(self) -> None:
        self.assert_syscall_mutation_rejected(
            "if (child < 0)\n\t\treturn child;",
            "if (child < 0 || va == 0)\n\t\treturn child;",
        )

    def test_rejects_wait4_copyout_before_taint(self) -> None:
        body = function_body(self.syscall, "sys_wait")
        merge = body.index("\tif (p->is_agent &&")
        copy = body.index("\telse\n\t\tresult = copyout", merge)
        leave = body.index("\tif (lifecycle_entered)", copy)
        mutated_body = body[:merge] + body[copy:leave] + body[merge:copy] + body[leave:]
        mutated = self.syscall.replace(body, mutated_body, 1)
        with self.assertRaises(ContractError):
            validate(mutated, self.ipc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
