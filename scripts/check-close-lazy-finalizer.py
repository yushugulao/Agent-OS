#!/usr/bin/env python3
"""Verify token-first close classification and lazy inode destruction."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


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


def ordered(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def check(root: Path) -> None:
    proc_header = compact(root / "os/proc.h")
    proc_source = compact(root / "os/proc.c")
    syscall_source = compact(root / "os/syscall.c")

    require(
        proc_header,
        "intfdclose_prepare(int,structfile_close_receipt*);",
        "fd detach receipt API is not public",
    )
    detach = function(proc_source, "fdclose_prepare")
    for fragment, message in (
        ("receipt->state!=FILE_CLOSE_RECEIPT_EMPTY",
         "fd detach can overwrite a prepared receipt"),
        ("enabled=intr_save()", "fd detach is not atomic"),
        ("(f=p->files[fd])==0", "fd detach does not capture one identity"),
        ("p->files[fd]=0", "fd detach does not unpublish the descriptor"),
        ("proc_clear_fd_delegate_tickets(p,fd)",
         "fd detach leaves delegation state published"),
        ("final=fileclose_prepare(f,receipt)",
         "fd detach does not release the captured identity"),
        ("returnfinal", "fd detach loses final-reference classification"),
    ):
        require(detach, fragment, message)
    reject(detach, "fileclose_finish", "fd detach runs a yielding destructor")
    if detach.find("p->files[fd]=0") < detach.find(
        "fileclose_prepare(f,receipt)"
    ):
        raise ContractError(
            "descriptor can be detached before its finalizer token is transferable"
        )
    ordered(
        detach,
        (
            "enabled=intr_save()",
            "f=p->files[fd]",
            "fileclose_prepare(f,receipt)",
            "if(final<0)",
            "p->files[fd]=0",
            "proc_clear_fd_delegate_tickets(p,fd)",
            "intr_restore(enabled)",
        ),
        "descriptor can be detached before its finalizer token is transferable",
    )

    wrapper = function(proc_source, "fdclose")
    ordered(
        wrapper,
        ("fdclose_prepare(fd,&receipt)", "fileclose_finish(&receipt)"),
        "legacy fdclose bypasses the shared receipt boundary",
    )

    may_io = function(syscall_source, "syscall_may_issue_block_io")
    epoch = function(syscall_source, "syscall_needs_fs_epoch")
    for body, label in ((may_io, "BIO"), (epoch, "FS epoch")):
        require(
            body,
            "caseSYS_close:return0",
            f"close still takes unconditional {label} admission",
        )

    begin = function(syscall_source, "syscall_transaction_begin")
    close_start = begin.find("if(transaction->id==SYS_close)")
    generic_start = begin.find("if(syscall_needs_fs_epoch(transaction))")
    if close_start < 0 or generic_start < 0 or close_start >= generic_start:
        raise ContractError("close is not detached before generic admission")
    close_path = begin[close_start:generic_start]
    reject(close_path, "fdget(", "close classifies before descriptor detach")
    for fragment, message in (
        ("transaction->args[0]>=FD_BUFFER_SIZE",
         "close narrows an unchecked raw descriptor"),
        ("fdclose_prepare((int)transaction->args[0],"
         "&transaction->close_receipt)",
         "close does not detach into its transaction receipt"),
        ("transaction->close_result=close_status<0?-1:0",
         "close loses invalid-descriptor status"),
        ("if(close_status<=0)return0",
         "non-final close enters the slow path"),
        ("transaction->close_final=1",
         "final close receipt is not retained across admission"),
        ("if(transaction->close_receipt.type!=FD_INODE)return0",
         "pipe or stdio close enters the filesystem slow path"),
    ):
        require(close_path, fragment, message)
    for fragment, message in (
        ("fs_epoch_request_begin()",
         "close eagerly enters the filesystem gate before drop-only classification"),
        ("bio_request_begin_current",
         "close eagerly reserves BIO capacity before drop-only classification"),
    ):
        reject(close_path, fragment, message)
    ordered(
        close_path,
        (
            "fdclose_prepare(",
            "if(close_status<=0)return0",
            "transaction->close_final=1",
            "if(transaction->close_receipt.type!=FD_INODE)return0",
        ),
        "close final-reference classification order is unsafe",
    )

    finish = function(syscall_source, "syscall_transaction_finish")
    for fragment, message in (
        ("if(transaction->close_final)",
         "close receipt is not recovered at syscall settlement"),
        ("receipt=&transaction->close_receipt",
         "close settlement consumes a copied or unrelated receipt"),
        ("if(final&&receipt->type!=FD_INODE){"
         "fileclose_finish(receipt);final=0;}",
         "non-inode final close lacks a direct destructor path"),
        ("fileclose_finish_drop_only(receipt)",
         "ordinary inode close does not attempt the admission-free path"),
        ("if(final&&!transaction->fs_epoch_admitted)",
         "admission failure can abandon a final inode receipt"),
        ("syscall_transaction_end_io(transaction);"
         "if(fs_epoch_request_begin()<0)",
         "slow close can enter the filesystem gate while holding an I/O lease"),
        ("fileclose_finish_epoch(receipt)",
         "reclaiming inode close bypasses the ordered epoch finalizer"),
        ("fileclose_finish_settle(receipt)",
         "deferred cleanup lease is never settled"),
        ("fileclose_finish(receipt)",
         "close receipt is never consumed"),
    ):
        require(finish, fragment, message)
    ordered(
        finish,
        (
            "receipt=&transaction->close_receipt",
            "transaction->close_final=0",
            "if(final&&receipt->type!=FD_INODE)",
            "fileclose_finish_drop_only(receipt)",
            "if(final&&!transaction->fs_epoch_admitted)",
            "fileclose_finish_epoch(receipt)",
            "syscall_transaction_end_io(transaction)",
            "fileclose_finish_settle(receipt)",
        ),
        "close receipt is consumed outside the unified settlement order",
    )

    dispatch = function(syscall_source, "syscall")
    require(
        dispatch,
        "caseSYS_close:ret=transaction.close_attempted?"
        "transaction.close_result:-1;",
        "close dispatch detaches or classifies a second descriptor",
    )
    reject(dispatch, "fdclose(", "close dispatch repeats descriptor detach")
    reject(dispatch, "sys_close(", "close dispatch bypasses its receipt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"close lazy-finalizer check failed: {error}", file=sys.stderr)
        return 1
    print("close lazy-finalizer check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
