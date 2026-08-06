#!/usr/bin/env python3
"""Check stable file identity across read/write syscall admission and use."""

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

    require(
        source,
        "structsyscall_transaction_context{intid;uint64args[6];"
        "structfile*file;structfile_close_receiptclose_receipt;"
        "intfd_uses_disk;",
        "syscall transaction does not own a stable file reference",
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
            "transaction->file=syscall_fd_pin(transaction->args[0])",
            "transaction->fd_uses_disk="
            "syscall_file_uses_disk(transaction->file)",
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

    dispatch = function(source, "syscall")
    for fragment, message in (
        (
            "sys_write(transaction.file,(int)args[0],args[1],args[2])",
            "write execution does not consume the admitted file",
        ),
        (
            "sys_read(transaction.file,(int)args[0],args[1],args[2])",
            "read execution does not consume the admitted file",
        ),
        (
            "if(syscall_transaction_begin(&transaction)<0){ret=-1;"
            "gotosyscall_out;}",
            "admission failure can bypass transaction cleanup",
        ),
        (
            "syscall_out:syscall_transaction_finish(&transaction,&ret)",
            "return paths bypass stable reference settlement",
        ),
    ):
        require(dispatch, fragment, message)

    may_io = function(source, "syscall_may_issue_block_io")
    epoch = function(source, "syscall_needs_fs_epoch")
    require(
        may_io,
        "caseSYS_read:caseSYS_write:returntransaction->fd_uses_disk",
        "read/write I/O admission does not use the stable classification",
    )
    require(
        epoch,
        "caseSYS_read:return0;caseSYS_write:"
        "returntransaction->fd_uses_disk",
        "pure read still acquires the filesystem mutation epoch",
    )
    for body, label in ((may_io, "I/O admission"), (epoch, "filesystem epoch")):
        require(
            body,
            "caseSYS_close:return0",
            f"close {label} still reserves before final-reference classification",
        )
        reject(body, "p->files", f"{label} reads a raw descriptor table slot")

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
        "close_status=fdclose_prepare((int)transaction->args[0],"
        "&transaction->close_receipt)",
        "close does not atomically detach into a release receipt",
    )
    require(
        begin,
        "if(transaction->close_receipt.type!=FD_INODE)return0;",
        "close lacks final-inode-only slow-path classification",
    )
    dispatch = function(source, "syscall")
    require(
        dispatch,
        "caseSYS_close:ret=transaction.close_attempted?"
        "transaction.close_result:-1;",
        "close dispatch detaches a second descriptor identity",
    )
    reject(dispatch, "sys_close(args[0])", "close bypasses its detached receipt")


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
