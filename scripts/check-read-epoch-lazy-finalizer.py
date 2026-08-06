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
    fs_epoch_header = compact(root / "os/fs_epoch.h")
    bio_source = compact(root / "os/bio.c")
    file_source = compact(root / "os/file.c")
    fs_source = compact(root / "os/fs.c")
    fs_epoch_source = compact(root / "os/fs_epoch.c")
    syscall_source = compact(root / "os/syscall.c")

    for fragment, message in (
        ("FILE_CLOSE_RECEIPT_EMPTY=0", "receipt lacks an empty state"),
        ("FILE_CLOSE_RECEIPT_PREPARED", "receipt lacks a prepared state"),
        ("FILE_CLOSE_RECEIPT_FINALIZING", "receipt lacks a finalizing state"),
        ("FILE_CLOSE_RECEIPT_SETTLEMENT", "receipt lacks a settlement state"),
        ("FILE_CLOSE_RECEIPT_CONSUMED", "receipt lacks a consumed state"),
        ("structbio_cleanup_tokencleanup_token;",
         "receipt omits its transferable cleanup token"),
        ("#defineFILE_CLOSE_BATCH_CAP1U",
         "teardown close settlement is not incrementally bounded"),
        ("structfile_close_batch{structbio_cleanup_token"
         "pending[FILE_CLOSE_BATCH_CAP];uintcount;};",
         "teardown close batch does not use its fixed settlement window"),
        ("intfileclose_finish_drop_only(structfile_close_receipt*);",
         "drop-only finalizer API is not public"),
        ("intfileclose_finish_epoch(structfile_close_receipt*);",
         "epoch finalizer API is not public"),
        ("intfileclose_finish_settle(structfile_close_receipt*);",
         "settlement API is not public"),
        ("intfileclose_batch_add(structfile_close_batch*,structfile*);",
         "teardown cannot append to the close batch"),
        ("intfileclose_batch_settle(structfile_close_batch*);",
         "teardown cannot settle a close batch outside the gate"),
    ):
        require(header, fragment, message)
    if header.count("uintcleanup_owner;") != 2:
        raise ContractError(
            "file object and close receipt do not share one cleanup owner"
        )
    for fragment, message in (
        ("structbio_cleanup_token{uintslot;uintgeneration;};",
         "cleanup token is not an opaque two-word handle"),
        ("intbio_cleanup_token_prepare(uint,structbio_cleanup_token*);",
         "cleanup prepare API is not public"),
        ("intbio_cleanup_token_sponsor(conststructbio_cleanup_token*,uint*,uint*);",
         "prepared cleanup sponsor cannot be inspected"),
        ("intbio_cleanup_token_begin(structbio_cleanup_token*);",
         "cleanup begin API is not public"),
        ("intbio_cleanup_token_end(structbio_cleanup_token*);",
         "cleanup end API is not public"),
        ("intbio_cleanup_sponsor_covers(uint,uint,uint64);",
         "epoch commit cannot reuse an exact cleanup sponsor"),
        ("intbio_cleanup_token_release(structbio_cleanup_token*,int);",
         "cleanup release API is not public"),
        ("voidbio_cache_retry_notify(void);",
         "ordinary close cannot notify blocked cache maintenance"),
        ("uintbio_process_owner(conststructproc*);",
         "file allocation cannot capture immutable I/O ownership"),
    ):
        require(bio_header, fragment, message)
    require(
        fs_epoch_header,
        "intfs_epoch_prepare_cleanup_sponsor(uint,uint);",
        "filesystem epoch lacks cleanup-sponsor compatibility preflight",
    )
    reject(
        bio_source,
        "BIO_CLEANUP_TOKEN_REUSE_REQUEST",
        "cleanup token can borrow a foreground request lease",
    )
    require(
        bio_source,
        "_Static_assert(sizeof(structbio_cleanup_token)==8",
        "cleanup token exceeds the stack budget",
    )
    retry_notify = function(bio_source, "bio_cache_retry_notify")
    require(retry_notify, "io_policy.background.cache_wait_pending",
            "ordinary close notifies without a pending background retry")
    require(retry_notify, "bio_cache_advance_progress_locked()",
            "cache retry notification does not publish progress")
    reject(retry_notify, "wait_queue_wake_all(&cache_waiters)",
           "ordinary close can broadcast-wake foreground cache waiters")
    require(retry_notify, "wait_queue_wake_one(&background_cache_waiter)",
            "cache retry notification is not isolated to its background waiter")
    reject(retry_notify, "bio_cleanup_token_",
           "cache retry notification is coupled to cleanup ownership")
    retry_wait = function(bio_source, "bio_background_wait_for_cache_progress")
    require(retry_wait,
            "wait_queue_sleep_irq_uninterruptible(&background_cache_waiter)",
            "background retry still shares the foreground cache queue")
    reject(retry_wait,
           "wait_queue_sleep_irq_uninterruptible(&cache_waiters)",
           "background retry can be consumed by a foreground wake")
    cache_progress = function(bio_source, "bio_cache_note_progress")
    require(cache_progress, "wait_queue_wake_all(&cache_waiters)",
            "real cache progress does not wake foreground waiters")
    require(cache_progress,
            "wait_queue_wake_one(&background_cache_waiter)",
            "real cache progress does not wake the background retry")
    require(bio_source,
            "wait_queue_init(&background_cache_waiter,"
            "WAIT_REASON_BUFFER_CACHE)",
            "background cache retry queue is not initialized")
    process_owner = function(bio_source, "bio_process_owner")
    require(process_owner, "lifecycle=vfs_proc_lifecycle(p)",
            "cleanup ownership does not follow immutable workflow identity")
    sponsor_begin = function(bio_source, "bio_deferred_sponsor_begin")
    reject(sponsor_begin, "!bio_cleanup_handle_empty(&io_policy.deferred.token)",
           "nested reclaim can borrow an unrelated cleanup-token class")
    require(sponsor_begin, "io_policy.deferred.io_class==io_class",
            "nested reclaim is not bound to its effective charged class")
    reject(sponsor_begin, "io_policy.deferred.retained_class==io_class",
           "nested reclaim can launder its retained class through another lease")
    sponsor_end = function(bio_source, "bio_deferred_sponsor_end")
    require(
        sponsor_end,
        "!bio_deferred_sponsor_current()||"
        "!bio_cleanup_handle_empty(&io_policy.deferred.token)",
        "ordinary deferred completion can consume a cleanup token",
    )
    sponsor_cover = function(bio_source, "bio_cleanup_sponsor_covers")
    for fragment, message in (
        ("origin_request_id==0",
         "cleanup sponsor coverage can reuse a foreground request"),
        ("bio_deferred_sponsor_current()",
         "cleanup sponsor coverage accepts an ambient identity"),
        ("!bio_cleanup_handle_empty(&io_policy.deferred.token)",
         "cleanup sponsor coverage accepts a tokenless lease"),
        ("io_policy.deferred.owner==owner",
         "cleanup sponsor coverage ignores the persistent owner"),
        ("io_policy.deferred.io_class==io_class",
         "cleanup sponsor coverage ignores the charged class"),
    ):
        require(sponsor_cover, fragment, message)

    sponsor_resolve = function(
        bio_source, "bio_cleanup_resolve_sponsor_locked"
    )
    sponsor_preview = function(bio_source, "bio_cleanup_token_sponsor")
    token_begin = function(bio_source, "bio_cleanup_token_begin")
    for body, message in (
        (sponsor_preview, "prepared cleanup preview diverges from begin"),
        (token_begin, "cleanup begin diverges from its prepared preview"),
    ):
        require(
            body,
            "bio_cleanup_resolve_sponsor_locked(",
            message,
        )
    require(
        sponsor_preview,
        "record->state!=BIO_CLEANUP_TOKEN_PREPARED",
        "cleanup sponsor preview accepts a mutable token state",
    )
    require(
        sponsor_preview,
        "*owner=record->owner",
        "cleanup sponsor preview does not expose the retained owner",
    )
    for fragment, message in (
        ("*effective_class=record->retained_class",
         "prepared cleanup does not keep an immutable charged class"),
        ("io_bucket_burst(record->owner,*effective_class)",
         "prepared cleanup does not validate its retained class"),
        ("*effective_class==IO_POLICY_CLASS_BACKGROUND",
         "cleanup can borrow a background executor for another class"),
        ("*execution_flag=BIO_CLEANUP_TOKEN_INDEPENDENT",
         "foreground cleanup lacks an independent lease"),
    ):
        require(sponsor_resolve, fragment, message)
    cleanup_prepare = function(bio_source, "bio_cleanup_token_prepare")
    require(
        cleanup_prepare,
        "bio_cleanup_class_select(owner,&retained_class)",
        "cleanup token does not select a stable asynchronous class",
    )
    reject(
        cleanup_prepare,
        "thread->io_request_",
        "cleanup token captures a foreground request identity",
    )
    require(
        token_begin,
        "io_policy.deferred.reuse_request_lease=0",
        "cleanup token can publish through a foreground request lease",
    )
    require(
        token_begin,
        "io_policy.deferred.origin_request_id=0",
        "cleanup token publishes a foreground origin identity",
    )

    epoch_commit = function(fs_epoch_source, "fs_epoch_commit")
    ordered(
        epoch_commit,
        (
            "if(!bio_cleanup_sponsor_covers(commit_owner,sponsor_class,"
            "sponsor_request_id))",
            "bio_deferred_sponsor_begin(commit_owner,sponsor_class,"
            "sponsor_request_id)",
            "sponsor_started=1",
        ),
        "epoch commit bypasses exact cleanup-sponsor matching",
    )
    require(epoch_commit, "if(sponsor_started)bio_deferred_sponsor_end()",
            "epoch commit consumes a borrowed cleanup token")
    epoch_fail = function(fs_epoch_source, "fs_epoch_commit_sponsored_fail")
    require(epoch_fail, "if(sponsor_started)bio_deferred_sponsor_end()",
            "epoch failure consumes a borrowed cleanup token")
    cleanup_preflight = function(
        fs_epoch_source, "fs_epoch_prepare_cleanup_sponsor"
    )
    for fragment, message in (
        ("epoch.owner==owner",
         "cleanup epoch compatibility ignores the persistent owner"),
        ("epoch.sponsor_class==io_class",
         "cleanup epoch compatibility ignores the charged class"),
        ("epoch.sponsor_request_id==0",
         "cleanup epoch compatibility can reuse a foreground request"),
        ("if(!dirty||compatible)return0;returnfs_epoch_commit()",
         "cleanup epoch preflight cannot batch an exact sponsor tuple"),
    ):
        require(cleanup_preflight, fragment, message)

    prepare = function(file_source, "fileclose_prepare")
    token_prepare_fn = function(file_source, "fileclose_cleanup_token_prepare")
    for fragment, message in (
        ("receipt->state!=FILE_CLOSE_RECEIPT_EMPTY",
         "prepare can overwrite an outstanding receipt"),
        ("receipt->cleanup_token=(structbio_cleanup_token)"
         "BIO_CLEANUP_TOKEN_INIT", "prepare does not initialize its token"),
        ("enabled=intr_save()", "reference drop is not atomic"),
        ("if(f->ref>1){f->ref--", "prepare does not atomically drop non-final refs"),
        ("f->type==FD_INODE&&f->ip->ref==1&&f->ip->valid&&f->ip->removed",
         "cleanup retention is not limited to destructive inode close"),
        ("fileclose_cleanup_token_prepare(f->cleanup_owner,"
         "&receipt->cleanup_token)",
         "known destructive close lacks cleanup-token retention"),
        ("receipt->type=f->type", "final receipt loses destructor identity"),
        ("receipt->ip=f->ip", "inode reference is not transferred to settlement"),
        ("receipt->cleanup_owner=f->cleanup_owner",
         "cleanup attribution is not transferred to settlement"),
        ("receipt->state=FILE_CLOSE_RECEIPT_PREPARED",
         "final receipt is not activated"),
        ("filepool_push_locked(index)", "final slot is not unpublished"),
    ):
        require(prepare, fragment, message)
    reject(prepare, "iput_drop_only(f->ip)",
           "prepare consumes an inode before unified settlement")
    require(token_prepare_fn, "bio_cleanup_token_prepare(owner,token)",
            "destructive inode close lacks cleanup-token retention")
    require(token_prepare_fn, "fileclose_cleanup_owner_valid(owner)",
            "cleanup token accepts an untrusted owner identity")
    reject(token_prepare_fn, "FS_OWNER_SYSTEM,token",
           "cleanup admission failure can launder work to the system owner")
    token_prepare = prepare.find(
        "fileclose_cleanup_token_prepare(f->cleanup_owner,"
        "&receipt->cleanup_token)"
    )
    final_detach = prepare.find("f->ref=0")
    if min(token_prepare, final_detach) < 0 or token_prepare > final_detach:
        raise ContractError("cleanup retention does not precede final slot publication")
    ordered(
        prepare,
        (
            "enabled=intr_save()",
            "f->type==FD_INODE&&f->ip->ref==1&&f->ip->valid&&f->ip->removed",
            "fileclose_cleanup_token_prepare(f->cleanup_owner,"
            "&receipt->cleanup_token)",
            "f->ref=0",
            "receipt->state=FILE_CLOSE_RECEIPT_PREPARED",
            "filepool_push_locked(index)",
            "intr_restore(enabled)",
        ),
        "cleanup retention does not precede final slot publication",
    )

    drop_only = function(file_source, "fileclose_finish_drop_only")
    require(drop_only, "iput_drop_only(receipt->ip)",
            "destructive close does not revalidate its inode classification")
    require(drop_only,
            "!fileclose_cleanup_token_empty(&receipt->cleanup_token)",
            "ordinary inode close tries to release an absent cleanup token")
    require(drop_only, "bio_cache_retry_notify()",
            "ordinary inode close does not wake blocked cache maintenance")
    ordered(
        drop_only,
        (
            "receipt->state=FILE_CLOSE_RECEIPT_FINALIZING",
            "iput_drop_only(receipt->ip)",
            "bio_cache_retry_notify()",
            "bio_cleanup_token_release(&receipt->cleanup_token,1)",
            "fileclose_receipt_complete(receipt)",
        ),
        "drop-only finalization releases ownership out of order",
    )

    epoch_finish = function(file_source, "fileclose_finish_epoch")
    for fragment, message in (
        ("!fs_epoch_request_held()", "inode destruction is not gate-bound"),
        ("fileclose_cleanup_token_prepare(receipt->cleanup_owner,"
         "&receipt->cleanup_token)",
         "late destructive classification cannot retain cleanup ownership"),
        ("bio_cleanup_token_sponsor(&receipt->cleanup_token,"
         "&sponsor_owner,&sponsor_class)",
         "destructive close cannot inspect its immutable cleanup sponsor"),
        ("fs_epoch_prepare_cleanup_sponsor(sponsor_owner,sponsor_class)",
         "destructive close cannot retire an incompatible epoch"),
        ("bio_cleanup_token_begin(&receipt->cleanup_token)",
         "inode destruction does not activate retained I/O ownership"),
        ("iput(receipt->ip)", "epoch finalizer omits inode destruction"),
        ("bio_cleanup_token_end(&receipt->cleanup_token)",
         "epoch finalizer leaks active I/O ownership"),
        ("receipt->state=FILE_CLOSE_RECEIPT_SETTLEMENT",
         "epoch finalizer cannot transfer deferred settlement"),
    ):
        require(epoch_finish, fragment, message)
    reject(
        epoch_finish,
        "fs_epoch_dirty()&&fs_epoch_commit()<0",
        "destructive close forces every reclaim epoch to flush",
    )
    if epoch_finish.count("fs_epoch_should_commit()&&fs_epoch_commit()<0") != 1:
        raise ContractError("destructive close bypasses bounded epoch batching")
    ordered(
        epoch_finish,
        (
            "fileclose_cleanup_token_prepare(receipt->cleanup_owner,"
            "&receipt->cleanup_token)",
            "bio_cleanup_token_sponsor(&receipt->cleanup_token,"
            "&sponsor_owner,&sponsor_class)",
            "fs_epoch_prepare_cleanup_sponsor(sponsor_owner,sponsor_class)",
            "bio_cleanup_token_begin(&receipt->cleanup_token)",
            "receipt->state=FILE_CLOSE_RECEIPT_FINALIZING",
            "iput(receipt->ip)",
            "fs_epoch_should_commit()&&fs_epoch_commit()<0",
            "bio_cleanup_token_end(&receipt->cleanup_token)",
            "receipt->state=FILE_CLOSE_RECEIPT_SETTLEMENT",
        ),
        "retained cleanup ownership does not cover inode destruction and commit",
    )
    reject(epoch_finish, "fs_deferred_reclaim_drain_current()",
           "close synchronously drains an owner-wide reclaim backlog")

    settle = function(file_source, "fileclose_finish_settle")
    require(settle, "fs_epoch_request_held()", "settlement ignores gate ownership")
    require(settle, "bio_cleanup_token_release(&receipt->cleanup_token,1)",
            "settlement does not release retained ownership")

    batch_transfer = function(file_source, "fileclose_batch_transfer")
    for fragment, message in (
        ("batch->count>=FILE_CLOSE_BATCH_CAP",
         "close batch can overflow its bounded token vector"),
        ("batch->pending[batch->count++]=receipt->cleanup_token",
         "close batch does not transfer cleanup-token identity"),
        ("receipt->cleanup_token=(structbio_cleanup_token)"
         "BIO_CLEANUP_TOKEN_INIT",
         "close batch leaves duplicate cleanup-token ownership"),
        ("fileclose_receipt_complete(receipt)",
         "close batch does not release the consumed file-slot charge"),
    ):
        require(batch_transfer, fragment, message)
    if batch_transfer.count("fileclose_receipt_complete(receipt)") != 1:
        raise ContractError(
            "close batch must consume its transferred receipt exactly once"
        )
    ordered(
        batch_transfer,
        (
            "batch->pending[batch->count++]=receipt->cleanup_token",
            "receipt->cleanup_token=(structbio_cleanup_token)"
            "BIO_CLEANUP_TOKEN_INIT",
            "fileclose_receipt_complete(receipt)",
        ),
        "close batch consumes its receipt before transferring token ownership",
    )

    batch_add = function(file_source, "fileclose_batch_add")
    for fragment, message in (
        ("fileclose_prepare(f,&receipt)",
         "close batch bypasses atomic file-slot detach"),
        ("fileclose_finish_drop_only(&receipt)",
         "close batch bypasses the ordinary inode fast path"),
        ("!fs_epoch_request_held()",
         "destructive batch close is not gate-bound"),
        ("fileclose_finish_epoch(&receipt)",
         "close batch omits destructive inode finalization"),
        ("receipt.state==FILE_CLOSE_RECEIPT_SETTLEMENT",
         "close batch cannot detect deferred BIO settlement"),
        ("fileclose_batch_transfer(batch,&receipt)",
         "close batch loses deferred settlement ownership"),
    ):
        require(batch_add, fragment, message)
    reject(batch_add, "fileclose(f)",
           "close batch falls back to the nested synchronous close wrapper")
    require(batch_add, "if(batch->count!=0)return1",
            "close batch can hoard more than one cleanup token")
    require(batch_add, "if(prepared<0)return1",
            "cleanup admission can wait while holding the filesystem gate")
    reject(batch_add,
           "while(prepared<0)",
           "cleanup admission busy-loops while holding the filesystem gate")

    batch_settle = function(file_source, "fileclose_batch_settle")
    require(batch_settle, "fs_epoch_request_held()",
            "close batch settlement ignores filesystem-gate ownership")
    require(batch_settle, "bio_cleanup_token_release(token,1)",
            "close batch settlement leaks cleanup-token ownership")
    require(batch_settle, "batch->count--",
            "close batch settlement cannot retire completed entries")

    allocate_many = function(file_source, "filealloc_many")
    require(allocate_many, "f->cleanup_owner=bio_process_owner(owner)",
            "file object does not retain its allocating I/O principal")

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
