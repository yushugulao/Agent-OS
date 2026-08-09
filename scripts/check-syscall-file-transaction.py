#!/usr/bin/env python3
"""检查读写 syscall 准入与使用期间稳定的文件身份。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def function(source: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", source)
    if match is None:
        raise ContractError(f"missing function: {name}")
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ContractError(f"unterminated function: {name}")


def require(source: str, fragment: str, message: str) -> None:
    if fragment not in source:
        raise ContractError(message)


def reject(source: str, fragment: str, message: str) -> None:
    if fragment in source:
        raise ContractError(message)


def require_order(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def check(root: Path) -> None:
    source = compact(root / "os/syscall.c")
    proc = compact(root / "os/proc.c")

    require(
        source,
        "structsyscall_transaction_context{intid;structfile*file;"
        "structfile_close_receiptclose_receipt;"
        "intclose_attempted;",
        "syscall transaction does not own a stable file reference",
    )
    reject(
        source,
        "structsyscall_transaction_context{intid;uint64args[6];",
        "slow transaction still copies all six syscall arguments",
    )

    pin = function(source, "syscall_fd_pin")
    for fragment, message in (
        ("fd>=FD_BUFFER_SIZE", "fd pinning omits raw-width validation"),
        ("returnfdget((int)fd)", "fd pinning is not an atomic strong reference"),
    ):
        require(pin, fragment, message)
    reject(pin, "p->files", "fd pinning reads the descriptor table without fdget")

    classify = function(source, "syscall_file_uses_disk")
    require(
        classify,
        "file!=0&&file->type==FD_INODE",
        "I/O classification does not consume the pinned file",
    )
    reject(classify, "fdget(", "I/O classification reacquires a descriptor")
    reject(classify, "p->files", "I/O classification reads a replaceable fd slot")

    prepare = function(source, "syscall_transaction_prepare")
    reject(
        prepare,
        "memset(transaction,0,sizeof(*transaction))",
        "hot syscall setup clears the cold close receipt",
    )
    require(
        prepare,
        "transaction->close_receipt.state=FILE_CLOSE_RECEIPT_EMPTY",
        "syscall setup does not initialize the lazy close receipt",
    )
    require_order(
        prepare,
        (
            "transaction->file=syscall_fd_pin(trapframe->a0)",
            "if(syscall_file_uses_disk(transaction->file))",
            "transaction->policy|=SYSCALL_POLICY_BLOCK_IO",
        ),
        "transaction does not pin before classifying the same file",
    )
    if prepare.count("syscall_fd_pin(") != 1:
        raise ContractError("transaction prepare does not perform exactly one fd pin")
    reject(prepare, "caseSYS_close", "close guesses a file identity before detach")

    for name, direction, access in (
        ("sys_read", "readable", "PTE_W"),
        ("sys_write", "writable", "PTE_R"),
    ):
        body = function(source, name)
        reject(body, "fdget(", f"{name} reacquires the descriptor")
        reject(body, "fileclose(", f"{name} releases the transaction-owned pin")
        for fragment, message in (
            ("structfile*f", f"{name} does not accept the stable file"),
            ("fd<0||fd>=FD_BUFFER_SIZE", f"{name} changed invalid-fd semantics"),
            ("f==NULL", f"{name} omits invalid descriptor handling"),
            (f"!f->{direction}", f"{name} omits direction validation"),
            ("len>MAX_RW_COUNT", f"{name} omits bounded length validation"),
            (
                f"user_range_check(p->pagetable,va,len,{access})<0",
                f"{name} omits user-range validation",
            ),
        ):
            require(body, fragment, message)

    dispatch = function(source, "syscall_dispatch")
    for fragment, message in (
        (
            "sys_write(transaction->file,(int)trapframe->a0,"
            "trapframe->a1,trapframe->a2)",
            "write execution does not consume the admitted file",
        ),
        (
            "sys_read(transaction->file,(int)trapframe->a0,"
            "trapframe->a1,trapframe->a2)",
            "read execution does not consume the admitted file",
        ),
    ):
        require(dispatch, fragment, message)

    slow = function(source, "syscall_slow_path")
    require(
        slow,
        "structsyscall_transaction_context*transaction="
        "(structsyscall_transaction_context*)"
        "thread_trap_cold(curr_thread())->syscall_transaction",
        "slow path does not use the current thread's trap-page scratch",
    )
    require_order(
        slow,
        (
            "syscall_transaction_prepare(transaction,trapframe,id,policy)",
            "syscall_transaction_begin(transaction,trapframe)",
            "syscall_dispatch(id,trapframe,transaction)",
            "syscall_transaction_finish(transaction,&ret)",
        ),
        "slow path can bypass transaction setup or settlement",
    )
    route = function(source, "syscall")
    require_order(
        route,
        (
            "id=syscall_decode_id(trapframe->a7)",
            "class=syscall_classify(id)",
            "kernel_work_begin_syscall(id,syscall_kernel_work_class(id))",
            "if(class==SYSCALL_CLASS_INVALID)",
            "policy=syscall_policy_base(class)",
            "if(syscall_needs_transaction(class))",
            "ret=syscall_slow_path(trapframe,id,policy)",
            "ret=syscall_dispatch(id,trapframe,0)",
        ),
        "syscall entry does not split fast and slow paths from one policy",
    )
    decode = function(source, "syscall_decode_id")
    require(
        decode,
        "if(raw_id>0x7fffffffULL)return-1;return(int)raw_id",
        "wide unknown syscall IDs can alias a registered low ID",
    )
    reject(route, "structsyscall_transaction_context", "fast entry owns a transaction")
    needs = function(source, "syscall_needs_transaction")
    require(
        needs,
        "class!=SYSCALL_CLASS_FAST&&class!=SYSCALL_CLASS_INVALID",
        "read/write/close can escape the descriptor transaction path",
    )

    registry = (root / "os/syscall_counter.h").read_text(encoding="utf-8")
    for name in ("read", "write", "close"):
        if not re.search(rf"\bX\({name},\s*DESCRIPTOR,\s*ALWAYS\)", registry):
            raise ContractError(f"{name} lacks descriptor transaction classification")
    for name in (
        "agent_file_edit_begin", "agent_file_edit_state", "agent_worker_create"
    ):
        if not re.search(
            rf"\bX\({name},\s*BLOCK_IO_FS_EPOCH,\s*ALWAYS\)", registry
        ):
            raise ContractError(
                f"{name} can drop a path inode without the filesystem epoch"
            )
    if not re.search(
        r"\bX\(agent_audit_receipt,\s*BLOCK_IO_FS_EPOCH,\s*ALWAYS\)", registry
    ):
        raise ContractError("audit receipt persistence lacks the filesystem epoch")
    fork = function(proc, "fork_common")
    snapshot = function(proc, "fd_spawn_snapshot_take")
    require_order(
        snapshot,
        (
            "issuer->fd_delegate_ticket[i]=0",
            "f==0||fd_is_reserved(f)",
            "f->inherit_class==FD_INHERIT_DENY",
            "!snapshot->delegated[i]",
            "snapshot->files[i]=filedup(f)",
        ),
        "派生快照仍会固定明确禁止或未委派的描述符",
    )
    require_order(
        fork,
        (
            "proc_vm_snapshot_begin(p)",
            "fd_spawn_snapshot_take(p,issuer,authority_boundary,&fds)",
            "fd_spawn_snapshot_release(&fds)",
            "add_task(nt)",
            "proc_vm_snapshot_end(p)",
            "intr_restore(publish_enabled)",
            "fail:fd_spawn_snapshot_release(&fds)",
            "proc_vm_snapshot_end(p)",
        ),
        "fork does not keep the parent snapshot across pinned-FD settlement",
    )
    if fork.count("proc_vm_snapshot_begin(p)") != 1 or \
            fork.count("proc_vm_snapshot_end(p)") != 2:
        raise ContractError("fork snapshot window has an unbalanced exit path")
    if fork.count("return-1;") != 3:
        raise ContractError("fork has an unreviewed failure exit")
    require_order(
        fork,
        (
            "workflow_lifecycle_operation_enter(parent_lifecycle)",
            "return-1;",
            "proc_vm_snapshot_begin(p)",
            "workflow_lifecycle_operation_leave(parent_lifecycle)",
            "return-1;",
            "fd_spawn_snapshot_take(p,issuer,authority_boundary,&fds)",
        ),
        "fork lifecycle admission is not settled before the parent snapshot",
    )
    policy = function(source, "syscall_policy_base")
    require(
        policy,
        "caseSYSCALL_CLASS_BLOCK_IO:returnSYSCALL_POLICY_BLOCK_IO",
        "block-I/O class is not translated into admission policy",
    )
    prepare = function(source, "syscall_transaction_prepare")
    require(
        prepare,
        "transaction->policy|=SYSCALL_POLICY_BLOCK_IO;"
        "if(transaction->id==SYS_write)"
        "transaction->policy|=SYSCALL_POLICY_FS_EPOCH",
        "pure read still acquires the filesystem mutation epoch",
    )
    reject(policy, "p->files", "policy reads a raw descriptor table slot")
    reject(prepare, "p->files", "prepare reads a raw descriptor table slot")

    begin = function(source, "syscall_transaction_begin")
    require_order(
        begin,
        ("fs_epoch_request_begin()", "bio_request_begin_current()"),
        "file transactions admit I/O before the filesystem epoch",
    )
    for fragment, message in (
        (
            "bio_request_begin_current_cleanup()==0",
            "failed admission lacks a final-reference cleanup reservation",
        ),
        (
            "transaction->io_cleanup_admitted=1",
            "cleanup reservation ownership is not recorded",
        ),
    ):
        require(begin, fragment, message)

    finish = function(source, "syscall_transaction_finish")
    require_order(
        finish,
        (
            "fileclose_prepare(transaction->file,receipt)",
		    "fileclose_finish_drop_only(receipt)",
            "syscall_transaction_end_io(transaction)",
            "fs_epoch_request_begin()",
		    "fileclose_finish_epoch(receipt)",
            "syscall_transaction_commit(transaction,result)",
            "fs_epoch_request_end()",
		    "fileclose_finish_settle(receipt)",
        ),
        "last inode reference does not enter the ordered lazy-finalizer path",
    )
    reject(
        finish,
        "fileclose(transaction->file)",
        "stable pin bypasses prepare/finish finalization",
    )
    require(
        finish,
        "transaction->file=0",
        "settled file reference can be released twice",
    )

    require(
        begin,
        "close_status=fdclose_prepare((int)trapframe->a0,"
        "&transaction->close_receipt)",
        "close does not atomically detach into a release receipt",
    )
    require(
        begin,
        "if(transaction->close_receipt.type!=FD_INODE)return0;",
        "close lacks final-inode-only slow-path classification",
    )
    dispatch = function(source, "syscall_dispatch")
    require(
        dispatch,
        "caseSYS_close:ret=transaction!=0&&transaction->close_attempted?"
        "transaction->close_result:-1;",
        "close dispatch detaches a second descriptor identity",
    )
    reject(dispatch, "sys_close(trapframe->a0)", "close bypasses its detached receipt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"syscall file transaction check failed: {error}", file=sys.stderr)
        return 1
    print("syscall file transaction check passed: stable fd identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
