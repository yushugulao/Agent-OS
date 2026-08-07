#!/usr/bin/env python3
"""检查有界传统 I/O 事务与工作记账路径。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


THREAD_COLD_ACCESS = re.compile(
    r"thread_trap_cold(?:_const)?\(\s*([A-Za-z_]\w*)\s*\)->"
)


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = THREAD_COLD_ACCESS.sub(r"\1->", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def function(text: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", text)
    if match is None:
        raise ValueError(f"missing function {name}")
    start = match.start()
    brace = text.find("{", match.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError(f"unterminated function {name}")


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def check(root: Path) -> None:
    counter_header_raw = (root / "os/syscall_counter.h").read_text(encoding="utf-8")
    header = compact(root / "os/kernel_work.h")
    work = compact(root / "os/kernel_work.c")
    file_source = compact(root / "os/file.c")
    fs_source = compact(root / "os/fs.c")
    pipe = compact(root / "os/pipe.c")
    syscall = compact(root / "os/syscall.c")
    trap = compact(root / "os/trap.c")
    proc = compact(root / "os/proc.c")
    riscv = compact(root / "os/riscv.h")

    for fragment, label in (
        ("#defineKERNEL_WORK_BYTES_PER_UNIT64U", "64-byte work granule"),
        ("#defineKERNEL_WORK_IO_BATCH_BYTES(16U*1024U)", "16 KiB batch"),
        ("uintkernel_work_units_from_bytes(uint64);", "normalization API"),
        ("intkernel_work_checkpoint_bytes(uint64bytes);", "byte checkpoint API"),
    ):
        require(header, fragment, f"kernel work lacks {label}")

    normalize = function(work, "kernel_work_units_from_bytes")
    require(
        normalize,
        "units=bytes/KERNEL_WORK_BYTES_PER_UNIT;"
        "if(bytes%KERNEL_WORK_BYTES_PER_UNIT!=0)units++;",
        "byte work normalization is not ceil(bytes / 64)",
    )
    require(
        normalize,
        "if(units>KERNEL_WORK_BUDGET_UNITS)returnKERNEL_WORK_BUDGET_UNITS;",
        "byte work normalization is not saturating",
    )
    byte_checkpoint = function(work, "kernel_work_checkpoint_bytes")
    require(
        byte_checkpoint,
        "returnkernel_work_checkpoint(kernel_work_units_from_bytes(bytes));",
        "byte checkpoint bypasses canonical work accounting",
    )
    checkpoint = function(work, "kernel_work_checkpoint_mode")
    require(
        checkpoint,
        "cold=thread_trap_cold(t);",
        "work checkpoint is not bound to the current thread's cold state",
    )
    reject(
        checkpoint,
        "get_cycle()",
        "traditional syscall checkpoints still sample the cycle counter",
    )
    require(
        checkpoint,
        "!cold->kernel_resched_pending&&"
        "cold->kernel_work_units<KERNEL_WORK_BUDGET_UNITS",
        "work checkpoint does not use the tick-published reschedule edge",
    )
    irq_window = function(work, "kernel_work_irq_window")
    require(
        irq_window,
        "if(t->kernel_work_target_syscall_id<0)return;",
        "IRQ delivery window can run outside a syscall safe-point scope",
    )
    require(
        irq_window,
        "if((r_sstatus()&(SSTATUS_SPP|SSTATUS_SIE))!=0)return;",
        "IRQ delivery window can re-enter a kernel trap or enabled section",
    )
    require(
        irq_window,
        "intr_delivery_window();",
        "long syscall does not provide a bounded device IRQ window",
    )
    delivery_window = function(riscv, "intr_delivery_window")
    require(
        delivery_window,
        'intr_on();asmvolatile("nop":::"memory");intr_off();',
        "architecture IRQ window does not restore the masked state",
    )
    budget_edge = checkpoint.find(
        "cold->kernel_work_units<KERNEL_WORK_BUDGET_UNITS"
    )
    delivery = checkpoint.find("kernel_work_irq_window(t);")
    peer_query = checkpoint.find("if(!scheduler_has_runnable_peer())")
    if min(budget_edge, delivery, peer_query) < 0 or not (
        budget_edge < delivery < peer_query
    ):
        raise ValueError(
            "IRQ delivery must remain off the short path and precede the "
            "runnable-peer query"
        )
    require(
        checkpoint,
        "if(!scheduler_has_runnable_peer()){"
        "cold->kernel_resched_pending=0;cold->kernel_work_units=0;",
        "uncontended kernel work still context-switches to itself",
    )
    peer = function(proc, "scheduler_has_runnable_peer")
    require(
        peer,
        "present=scheduler_active_domains.count!=0;",
        "runnable-peer query does not use the scheduler's active-domain index",
    )
    require(
        trap,
        "kernel_work_request_resched();",
        "timer trap does not publish the kernel reschedule edge",
    )
    require(
        fs_source,
        "#ifdefined(LOG_LEVEL_DEBUG)||defined(LOG_LEVEL_TRACE)",
        "inode mapping lock diagnostics are not confined to debug builds",
    )
    require(
        fs_source,
        "#defineinode_mapping_require(ip,write)",
        "production inode I/O still executes lock-diagnostic bookkeeping",
    )
    require(
        fs_source,
        "#defineFS_OVERWRITE_EPOCH_CREDITS1U",
        "existing-block writes still reserve the full allocation epoch",
    )
    write_prepare = function(fs_source, "writei_prepare_locked")
    require(
        write_prepare,
        "fs_claim_sponsored_public_inode(ip,cred)<0",
        "PUBLIC storage ownership is not settled by the write planner",
    )
    write_locked = function(fs_source, "writei_charged_locked")
    reject(
        write_locked,
        "fs_claim_sponsored_public_inode(",
        "PUBLIC storage ownership can block inside the atomic I/O section",
    )
    require(
        write_locked,
        "if(first_plan!=0&&first_plan->valid)",
        "the first mapped write plan is recomputed inside the atomic section",
    )
    write_auth = function(fs_source, "writei_with_auth")
    validation = write_auth.find("writei_prepare_locked(")
    planning = write_auth.find("writei_plan_prepare(")
    atomic = write_auth.find("bio_fs_atomic_enter();")
    if min(validation, planning, atomic) < 0 or not validation < planning < atomic:
        raise ValueError(
            "write authorization, ownership transfer, and mapping plan are not "
            "completed before atomic I/O"
        )

    require(
        file_source,
        "structinode_io_transaction{structfile*file;structinode*inode;"
        "structvfs_credcred;structopen_file_io_tokenlease;"
        "uint64user_base;uint64total;uint64done;};",
        "traditional inode I/O lacks a per-syscall transaction context",
    )
    begin = function(file_source, "inode_io_transaction_begin")
    require(
        begin,
        "open_file_io_lease_acquire(file,operation,&transaction->lease,"
        "&transaction->cred)<0",
        "I/O transaction bypasses the generation-bound authorization lease",
    )
    batch = function(file_source, "inode_io_transaction_batch")
    require(
        batch,
        "limit=KERNEL_WORK_IO_BATCH_BYTES-alignment;"
        "return(uint)MIN(remaining,limit);",
        "I/O transaction is not bounded by the aligned 16 KiB batch",
    )

    for name, primitive in (
        ("inodewrite", "writei_lease"),
        ("inoderead", "readi_lease"),
    ):
        body = function(file_source, name)
        require(
            body,
            "inode_io_transaction_begin(&transaction,f,va,len,",
            f"{name} bypasses the shared transaction begin",
        )
        require(
            body,
            "chunk=inode_io_transaction_batch(&transaction);",
            f"{name} bypasses the bounded batch",
        )
        require(
            body,
            f"{primitive}(transaction.inode,&transaction.cred,"
            "&transaction.lease,1,user_addr,",
            f"{name} rebuilds authority instead of reusing the transaction",
        )
        require(
            body,
            "open_file_io_token_end(&transaction.lease);",
            f"{name} leaks a syscall-scoped authorization token",
        )
        require(
            body,
            "kernel_work_checkpoint_bytes((uint)r);",
            f"{name} does not normalize byte work",
        )
        reject(
            body,
            "kernel_work_checkpoint((uint)r)",
            f"{name} charges bytes directly as CPU work",
        )
        reject(
            body,
            "BSIZE-offset%BSIZE",
            f"{name} regressed to one filesystem call per block",
        )
        reject(
            body,
            "vfs_cred_from_proc(",
            f"{name} rebuilds the credential inside the batch loop",
        )

    if pipe.count("kernel_work_checkpoint_bytes(size)") != 2:
        raise ValueError("pipe byte work is not normalized on read and write")
    reject(pipe, "kernel_work_checkpoint((uint)size)", "pipe charges bytes as CPU work")
    require(
        syscall,
        "kernel_work_checkpoint_bytes(KERNEL_WORK_STREAM_GRANULE)",
        "console byte work is not normalized",
    )
    fstat = function(syscall, "sys_fstat")
    require(
        fstat,
        "f->ip!=0&&f->ip->valid",
        "cached fstat does not enforce the open-inode invariant",
    )
    reject(fstat, "ivalid(", "fstat can issue block I/O from its fast class")

    if syscall.count("syscall_fd_pin(") != 2:
        raise ValueError("descriptor object is not pinned exactly once")
    if syscall.count("syscall_file_uses_disk(") != 2:
        raise ValueError("pinned file classification is not single-pass")
    prepare = function(syscall, "syscall_transaction_prepare")
    require(
        prepare,
        "transaction->file=syscall_fd_pin(trapframe->a0);",
        "syscall transaction does not pin the descriptor object",
    )
    require(
        prepare,
        "if(syscall_file_uses_disk(transaction->file)){"
        "transaction->policy|=SYSCALL_POLICY_BLOCK_IO;",
        "syscall transaction does not classify the pinned object once",
    )
    classify = function(syscall, "syscall_classify")
    policy = function(syscall, "syscall_policy_base")
    require(
        classify,
        "intslot=syscall_counter_slot(id);"
        "if(slot<0)returnSYSCALL_CLASS_INVALID;"
        "return(enumsyscall_class)syscall_class_by_slot[slot];",
        "syscall classification is not a bounded registry lookup",
    )
    require(
        prepare,
        "if(transaction->id==SYS_write)"
        "transaction->policy|=SYSCALL_POLICY_FS_EPOCH;",
        "inode writes do not enter the ordered mutation epoch",
    )
    reject(syscall, "syscall_may_issue_block_io(",
           "dispatcher retained the duplicate I/O classification switch")
    reject(syscall, "syscall_needs_fs_epoch(",
           "dispatcher retained the duplicate epoch classification switch")
    reject(
        syscall,
        "uint64args[6]",
        "syscall path still copies all six trapframe arguments",
    )
    route = function(syscall, "syscall")
    for fragment, message in (
        (
            "class=syscall_classify(id);",
            "syscall entry does not classify the registered ID once",
        ),
        (
            "kernel_work_begin_syscall(id,syscall_kernel_work_class(id));"
            "if(class==SYSCALL_CLASS_INVALID)ret=-1;"
            "else{uintpolicy=syscall_policy_base(class);"
            "if(syscall_needs_transaction(class))"
            "ret=syscall_slow_path(trapframe,id,policy);"
            "elseret=syscall_dispatch(id,trapframe,0);}",
            "unknown syscall can enter dispatch or the transaction slow path",
        ),
    ):
        require(route, fragment, message)
    require(
        route,
        "id=syscall_decode_id(trapframe->a7);",
        "syscall entry truncates the raw syscall number",
    )
    reject(
        route,
        "structsyscall_transaction_context",
        "fast syscall entry still reserves a transaction stack frame",
    )
    needs = function(syscall, "syscall_needs_transaction")
    require(
        needs,
        "class!=SYSCALL_CLASS_FAST&&class!=SYSCALL_CLASS_INVALID;",
        "registered syscall classes do not determine the slow path",
    )
    slow = function(syscall, "syscall_slow_path")
    require(
        slow,
        "structsyscall_transaction_context*transaction="
        "(structsyscall_transaction_context*)"
        "thread_trap_cold(curr_thread())->syscall_transaction;",
        "slow path does not reuse the current thread's trap-page scratch",
    )
    require(
        slow,
        "syscall_transaction_finish(transaction,&ret);",
        "slow path can return without settling the transaction",
    )
    dispatch = function(syscall, "syscall_dispatch")
    registry_entries = re.findall(
        r"\bX\(\s*([a-zA-Z0-9_]+)\s*,\s*([A-Z0-9_]+)\s*,"
        r"\s*([A-Z0-9_]+)\s*\)",
        counter_header_raw,
    )
    registered = {name for name, _class, _enabled in registry_entries}
    if len(registry_entries) != len(registered):
        raise ValueError("syscall registry contains duplicate names")
    allowed_classes = {
        "FAST", "DESCRIPTOR", "BLOCK_IO", "FS_EPOCH", "BLOCK_IO_FS_EPOCH"
    }
    allowed_features = {
        "ALWAYS", "VIRTIO_TEST", "PHYSICAL_PAGE_TEST", "METADATA_TEST",
        "WAIT_ATOMIC_TEST", "FS_ALLOCATOR_TEST",
    }
    for name, class_name, enabled in registry_entries:
        if class_name not in allowed_classes or enabled not in allowed_features:
            raise ValueError(
                f"syscall {name} lacks an explicit supported class or feature gate"
            )
    classes = {name: class_name for name, class_name, _enabled in registry_entries}
    expected_fast = {
        "fstat", "agent_create", "agent_create_role", "agent_workflow_create",
        "agent_scope_delegate_fd", "agent_workflow_lifecycle_info",
        "agent_sched_config",
        "context_push", "context_query", "context_snapshot", "context_detail",
        "context_rollback", "context_clear", "agent_file_edit_commit",
        "agent_file_edit_abort",
    }
    if any(classes.get(name) != "FAST" for name in expected_fast):
        raise ValueError("bounded cached syscalls regressed into transaction admission")
    expected_read_io = {"agent_file_query"}
    if any(classes.get(name) != "BLOCK_IO" for name in expected_read_io):
        raise ValueError("read-only Agent I/O acquired the filesystem mutation epoch")
    expected_edit_io = {
        "agent_file_edit_begin", "agent_file_edit_state", "agent_worker_create",
    }
    if any(classes.get(name) != "BLOCK_IO_FS_EPOCH" for name in expected_edit_io):
        raise ValueError("Agent object cleanup can release an inode without an FS epoch")
    expected_gates = {
        "virtio_disk_test": "VIRTIO_TEST",
        "physical_page_test": "PHYSICAL_PAGE_TEST",
        "agent_metadata_test": "METADATA_TEST",
        "wait_atomic_test": "WAIT_ATOMIC_TEST",
        "fs_allocator_fault_test": "FS_ALLOCATOR_TEST",
    }
    actual_gates = {
        name: enabled
        for name, _class, enabled in registry_entries
        if enabled != "ALWAYS"
    }
    if actual_gates != expected_gates:
        raise ValueError(f"syscall feature gates drifted: {actual_gates}")
    dispatched = set(re.findall(r"caseSYS_([a-zA-Z0-9_]+):", dispatch))
    if registered != dispatched:
        missing = sorted(dispatched - registered)
        stale = sorted(registered - dispatched)
        raise ValueError(
            f"compact syscall counters drifted: missing={missing}, stale={stale}"
        )
    syscall_ids = {
        name: int(value)
        for name, value in re.findall(
            r"^#define\s+SYS_([A-Za-z0-9_]+)\s+([0-9]+)\s*$",
            (root / "os/syscall_ids.h").read_text(encoding="utf-8"),
            flags=re.M,
        )
    }
    registered_ids = [syscall_ids[name] for name in registered]
    if len(registered_ids) != len(set(registered_ids)):
        raise ValueError("registered syscalls contain duplicate numeric IDs")
    require(
        syscall,
        "staticconstucharsyscall_class_by_slot[SYSCALL_COUNTER_SLOTS]",
        "syscall registry does not generate the runtime class table",
    )
    for fragment in (
        "#defineSYSCALL_COUNTER_MAP(name,class,enabled)",
        "[SYS_##name]=SYSCALL_ENABLED_##enabled?",
        "SYSCALL_REGISTERED(SYSCALL_COUNTER_MAP)",
        "#defineSYSCALL_CLASS_MAP(name,class,enabled)",
        "[SYSCALL_COUNTER_SLOT_##name]=SYSCALL_CLASS_##class,",
        "SYSCALL_REGISTERED(SYSCALL_CLASS_MAP)",
    ):
        require(
            syscall,
            fragment,
            "runtime class table is not generated from the closed registry",
        )
    reject(
        dispatch,
        "uint64args[6]",
        "dispatcher rebuilds a six-argument snapshot",
    )
    require(
        dispatch,
        "trapframe->a0",
        "dispatcher does not consume trapframe arguments directly",
    )
    admission = function(syscall, "syscall_transaction_begin")
    generic = admission.find(
        "if((transaction->policy&SYSCALL_POLICY_FS_EPOCH)!=0)"
    )
    if generic < 0:
        raise ValueError("syscall transaction lacks generic epoch admission")
    generic_admission = admission[generic:]
    if generic_admission.find("fs_epoch_request_begin()") > generic_admission.find(
        "bio_request_begin_current()"
    ):
        raise ValueError("syscall transaction admits I/O before its epoch")
    finish = function(syscall, "syscall_transaction_finish")
    for fragment in (
        "fileclose_prepare(transaction->file,receipt);",
        "fileclose_finish_drop_only(receipt);",
        "fileclose_finish_epoch(receipt)",
        "fileclose_finish_settle(receipt)",
        "syscall_transaction_commit(transaction,result);",
        "fs_epoch_request_end();",
        "syscall_transaction_end_io(transaction);",
    ):
        require(finish, fragment, "syscall transaction finish is incomplete")
    slow_end = finish.find("syscall_transaction_end_io(transaction);")
    gate = finish.find("fs_epoch_request_begin()", slow_end)
    inode_finish = finish.find("fileclose_finish_epoch(receipt)", gate)
    release = finish.find("fs_epoch_request_end();", inode_finish)
    settle = finish.rfind("syscall_transaction_end_io(transaction);")
    deferred = finish.find("fileclose_finish_settle(receipt)", settle)
    if min(slow_end, gate, inode_finish, release, settle, deferred) < 0 or not (
        slow_end < gate < inode_finish < release < settle < deferred
    ):
        raise ValueError("lazy inode finalizer release order is unsafe")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"traditional I/O fast-path check failed: {error}", file=sys.stderr)
        return 1
    print("traditional I/O fast-path check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
