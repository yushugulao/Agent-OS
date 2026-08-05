#!/usr/bin/env python3
"""Check the bounded traditional-I/O transaction and work-accounting path."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
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
    header = compact(root / "os/kernel_work.h")
    work = compact(root / "os/kernel_work.c")
    file_source = compact(root / "os/file.c")
    pipe = compact(root / "os/pipe.c")
    syscall = compact(root / "os/syscall.c")

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
    require(checkpoint, "now=get_cycle();", "work checkpoint lacks cycle sampling")
    require(
        checkpoint,
        "now<t->kernel_slice_deadline",
        "work checkpoint does not enforce the dispatch deadline",
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

    if syscall.count("syscall_fd_pin(") != 2:
        raise ValueError("descriptor object is not pinned exactly once")
    if syscall.count("syscall_file_uses_disk(") != 2:
        raise ValueError("pinned file classification is not single-pass")
    prepare = function(syscall, "syscall_transaction_prepare")
    require(
        prepare,
        "transaction->file=syscall_fd_pin(transaction->args[0]);",
        "syscall transaction does not pin the descriptor object",
    )
    require(
        prepare,
        "transaction->fd_uses_disk=syscall_file_uses_disk(transaction->file);",
        "syscall transaction does not classify the pinned object",
    )
    may_io = function(syscall, "syscall_may_issue_block_io")
    epoch = function(syscall, "syscall_needs_fs_epoch")
    require(
        may_io,
        "caseSYS_read:caseSYS_write:returntransaction->fd_uses_disk;",
        "read/write I/O admission rescans the descriptor classification",
    )
    require(
        epoch,
        "caseSYS_read:return0;caseSYS_write:returntransaction->fd_uses_disk;",
        "pure reads still serialize on the mutation epoch",
    )
    for body, name in ((may_io, "syscall_may_issue_block_io"),
                       (epoch, "syscall_needs_fs_epoch")):
        reject(body, "syscall_file_uses_disk(",
               f"{name} performs a second descriptor classification")
    admission = function(syscall, "syscall_transaction_begin")
    generic = admission.find("if(syscall_needs_fs_epoch(transaction))")
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
