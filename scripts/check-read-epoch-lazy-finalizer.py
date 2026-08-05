#!/usr/bin/env python3
"""Verify lock ordering and exactly-once lazy file finalization."""

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
    header = compact(root / "os/file.h")
    bio_header = compact(root / "os/bio.h")
    bio_source = compact(root / "os/bio.c")
    file_source = compact(root / "os/file.c")
    fs_source = compact(root / "os/fs.c")
    syscall_source = compact(root / "os/syscall.c")

    for fragment, message in (
        ("FILE_CLOSE_RECEIPT_EMPTY=0", "receipt lacks an empty state"),
        ("FILE_CLOSE_RECEIPT_PREPARED", "receipt lacks a prepared state"),
        ("FILE_CLOSE_RECEIPT_FINALIZING", "receipt lacks a finalizing state"),
        ("FILE_CLOSE_RECEIPT_SETTLEMENT", "receipt lacks a settlement state"),
        ("FILE_CLOSE_RECEIPT_CONSUMED", "receipt lacks a consumed state"),
        ("structbio_cleanup_tokencleanup_token;",
         "receipt omits its transferable cleanup token"),
        ("intfileclose_finish_drop_only(structfile_close_receipt*);",
         "drop-only finalizer API is not public"),
        ("intfileclose_finish_epoch(structfile_close_receipt*);",
         "epoch finalizer API is not public"),
        ("intfileclose_finish_settle(structfile_close_receipt*);",
         "settlement API is not public"),
    ):
        require(header, fragment, message)
    for fragment, message in (
        ("structbio_cleanup_token{uintslot;uintgeneration;};",
         "cleanup token is not an opaque two-word handle"),
        ("intbio_cleanup_token_prepare(uint,structbio_cleanup_token*);",
         "cleanup prepare API is not public"),
        ("intbio_cleanup_token_begin(structbio_cleanup_token*);",
         "cleanup begin API is not public"),
        ("intbio_cleanup_token_end(structbio_cleanup_token*);",
         "cleanup end API is not public"),
        ("intbio_cleanup_token_release(structbio_cleanup_token*,int);",
         "cleanup release API is not public"),
    ):
        require(bio_header, fragment, message)
    require(
        bio_source,
        "_Static_assert(sizeof(structbio_cleanup_token)==8",
        "cleanup token exceeds the stack budget",
    )

    prepare = function(file_source, "fileclose_prepare")
    for fragment, message in (
        ("receipt->state!=FILE_CLOSE_RECEIPT_EMPTY",
         "prepare can overwrite an outstanding receipt"),
        ("receipt->cleanup_token=(structbio_cleanup_token)"
         "BIO_CLEANUP_TOKEN_INIT", "prepare does not initialize its token"),
        ("enabled=intr_save()", "reference drop is not atomic"),
        ("if(f->ref>1){f->ref--", "prepare does not atomically drop non-final refs"),
        ("bio_cleanup_token_prepare(owner,&receipt->cleanup_token)",
         "final inode lacks cleanup-token retention"),
        ("bio_cleanup_token_prepare(FS_OWNER_SYSTEM,"
         "&receipt->cleanup_token)",
         "untrusted inode ownership lacks system fallback"),
        ("receipt->state=FILE_CLOSE_RECEIPT_PREPARED",
         "final receipt is not activated"),
        ("filepool_push_locked(index)", "final slot is not unpublished"),
    ):
        require(prepare, fragment, message)
    token_prepare = prepare.find(
        "bio_cleanup_token_prepare(owner,&receipt->cleanup_token)"
    )
    final_detach = prepare.find("f->ref=0")
    if min(token_prepare, final_detach) < 0 or token_prepare > final_detach:
        raise ContractError("cleanup retention does not precede final slot publication")
    ordered(
        prepare,
        (
            "enabled=intr_save()",
            "bio_cleanup_token_prepare(owner,&receipt->cleanup_token)",
            "f->ref=0",
            "receipt->state=FILE_CLOSE_RECEIPT_PREPARED",
            "filepool_push_locked(index)",
            "intr_restore(enabled)",
        ),
        "cleanup retention does not precede final slot publication",
    )

    drop_only = function(file_source, "fileclose_finish_drop_only")
    require(drop_only, "iput_drop_only(receipt->ip)",
            "ordinary final close bypasses the drop-only fast path")
    ordered(
        drop_only,
        (
            "receipt->state=FILE_CLOSE_RECEIPT_FINALIZING",
            "iput_drop_only(receipt->ip)",
            "bio_cleanup_token_release(&receipt->cleanup_token,1)",
            "fileclose_receipt_complete(receipt)",
        ),
        "drop-only finalization releases ownership out of order",
    )

    epoch_finish = function(file_source, "fileclose_finish_epoch")
    for fragment, message in (
        ("!fs_epoch_request_held()", "inode destruction is not gate-bound"),
        ("bio_cleanup_token_begin(&receipt->cleanup_token)",
         "inode destruction does not activate retained I/O ownership"),
        ("iput(receipt->ip)", "epoch finalizer omits inode destruction"),
        ("bio_cleanup_token_end(&receipt->cleanup_token)",
         "epoch finalizer leaks active I/O ownership"),
        ("receipt->state=FILE_CLOSE_RECEIPT_SETTLEMENT",
         "epoch finalizer cannot transfer deferred settlement"),
    ):
        require(epoch_finish, fragment, message)
    ordered(
        epoch_finish,
        (
            "bio_cleanup_token_begin(&receipt->cleanup_token)",
            "receipt->state=FILE_CLOSE_RECEIPT_FINALIZING",
            "iput(receipt->ip)",
            "fs_epoch_commit()",
            "bio_cleanup_token_end(&receipt->cleanup_token)",
            "receipt->state=FILE_CLOSE_RECEIPT_SETTLEMENT",
        ),
        "retained cleanup ownership does not cover inode destruction and commit",
    )

    settle = function(file_source, "fileclose_finish_settle")
    require(settle, "fs_epoch_request_held()", "settlement ignores gate ownership")
    require(settle, "bio_cleanup_token_release(&receipt->cleanup_token,1)",
            "settlement does not release retained ownership")

    wrapper = function(file_source, "fileclose")
    ordered(
        wrapper,
        ("fileclose_prepare(f,&receipt)", "fileclose_finish(&receipt)"),
        "legacy fileclose bypasses the receipt mechanism",
    )

    iput_fast = function(fs_source, "iput_drop_only")
    require(iput_fast, "inode_cache_drop_ref(ip)",
            "drop-only fast path does not release the inode reference")
    reject(iput_fast, "inode_remove_detach(",
           "drop-only fast path can perform destructive reclamation")

    may_io = function(syscall_source, "syscall_may_issue_block_io")
    epoch = function(syscall_source, "syscall_needs_fs_epoch")
    require(
        may_io,
        "caseSYS_read:caseSYS_write:returntransaction->fd_uses_disk",
        "inode reads bypass block-I/O admission",
    )
    require(
        epoch,
        "caseSYS_read:return0;caseSYS_write:returntransaction->fd_uses_disk",
        "pure inode reads still acquire the mutation epoch",
    )

    transaction_finish = function(syscall_source, "syscall_transaction_finish")
    reject(
        transaction_finish,
        "fileclose(transaction->file)",
        "transaction pin bypasses receipt preparation",
    )
    for fragment, message in (
        ("final=fileclose_prepare(transaction->file,receipt)",
         "transaction pin is not atomically released"),
        ("fileclose_finish_drop_only(receipt)",
         "transaction misses the ordinary-close fast path"),
        ("if(final&&!transaction->fs_epoch_admitted)",
         "inode finalizer lacks a conditional slow path"),
        ("fileclose_finish_epoch(receipt)",
         "transaction bypasses token-backed inode finalization"),
        ("fileclose_finish_settle(receipt)",
         "transaction can abandon transferred cleanup ownership"),
    ):
        require(transaction_finish, fragment, message)

    slow = transaction_finish.find("if(final&&!transaction->fs_epoch_admitted)")
    end_before_gate = transaction_finish.find(
        "syscall_transaction_end_io(transaction)", slow
    )
    gate = transaction_finish.find("fs_epoch_request_begin()", end_before_gate)
    inode_finish = transaction_finish.find("fileclose_finish_epoch(receipt)", gate)
    commit = transaction_finish.find(
        "syscall_transaction_commit(transaction,result)", inode_finish
    )
    gate_end = transaction_finish.find("fs_epoch_request_end()", commit)
    debt_end = transaction_finish.rfind("syscall_transaction_end_io(transaction)")
    settle_pos = transaction_finish.find("fileclose_finish_settle(receipt)", debt_end)
    if min(slow, end_before_gate, gate, inode_finish, commit,
           gate_end, debt_end, settle_pos) < 0 or not (
        slow < end_before_gate < gate < inode_finish < commit
        < gate_end < debt_end < settle_pos
    ):
        raise ContractError(
            "inode slow path violates FS-gate/BIO ordering or loses its receipt"
        )
    if "syscall_transaction_end_io(transaction)" in transaction_finish[
        commit:gate_end
    ]:
        raise ContractError(
            "inode slow path violates FS-gate/BIO ordering or loses its receipt"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"read epoch lazy-finalizer check failed: {error}", file=sys.stderr)
        return 1
    print("read epoch lazy-finalizer check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
