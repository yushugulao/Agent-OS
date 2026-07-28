#!/usr/bin/env python3
"""Fail closed when the brk path bypasses VM resource accounting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
syscall = (ROOT / "os/syscall.c").read_text(encoding="utf-8")
proc = (ROOT / "os/proc.c").read_text(encoding="utf-8")
proc_h = (ROOT / "os/proc.h").read_text(encoding="utf-8")
loader = (ROOT / "os/loader.c").read_text(encoding="utf-8")
loader_h = (ROOT / "os/loader.h").read_text(encoding="utf-8")
vm = (ROOT / "os/vm.c").read_text(encoding="utf-8")
guest = (ROOT / "user/src/physicalresource_ucore.c").read_text(encoding="utf-8")


def require(text: str, tokens: tuple[str, ...], component: str) -> None:
    for token in tokens:
        if token not in text:
            raise SystemExit(f"{component}: missing {token}")


require(
    syscall,
    (
        "static int sys_sbrk(uint64 raw_delta)",
        "return proc_sbrk((long)raw_delta);",
        "case SYS_brk:",
        "ret = sys_sbrk(args[0]);",
    ),
    "syscall",
)
if syscall.count("case SYS_brk:") < 2:
    raise SystemExit("syscall: brk is not explicitly I/O-free and dispatched")

require(proc_h, ("uint64 heap_base;", "uint64 heap_break;", "int proc_sbrk(long);"), "proc ABI")
require(
    proc,
    (
        "if (proc_vm_snapshot_begin(p) < 0)",
        "kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS)",
        "rollback_growth:",
        "uvm_unmap_reclaim(pagetable, old_end, mapped_pages);",
        "np->heap_base = p->heap_base;",
        "np->heap_break = p->heap_break;",
        "p->heap_base = image->heap_base;",
        "p->heap_break = image->heap_break;",
    ),
    "proc brk",
)
require(
    vm,
    (
        "memset(mem, 0, PGSIZE);",
        "static int uvm_prune_empty_walk(",
        "void uvm_unmap_reclaim(",
        "kfree_account_page(child, account, charge_class)",
    ),
    "VM refund",
)
require(
    loader_h + loader,
    (
        "USER_HEAP_LIMIT",
        "image->heap_base = image->ustack_base +",
        "NTHREAD * USTACK_SIZE + PAGE_SIZE",
        "image->heap_break = image->heap_base;",
    ),
    "image layout",
)
require(
    guest,
    (
        "check_brk_contract();",
        "physical_class_usage(&peak) >= physical_class_usage(&before) + 2",
        "physical_class_usage(&after) == physical_class_usage(&before)",
        "fork inherits exact program break and heap bytes",
        "full physical account rejects additional pages",
        "brk_atomic=1 fork_inherit=1 shrink_refund=1 guard=1",
    ),
    "Guest regression",
)
print("[physical-resource] brk static wiring passed")
