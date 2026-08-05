#!/usr/bin/env python3
"""Check inode mapping and shared open-offset serialization boundaries."""

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
    match = re.search(
        rf"(?:static)?(?:int|uint|uint64|void){re.escape(name)}"
        rf"\([^;{{}}]*\)\{{",
        source,
    )
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


def ordered(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def check(root: Path) -> None:
    fs_source = compact(root / "os/fs.c")
    file_source = compact(root / "os/file.c")

    for fragment, message in (
        ("structinode_mapping_guard{uintreaders;uintreader_waiters;"
         "uintwriter_waiters;uintwriter_depth;void*writer;"
         "uint64writer_generation;}",
         "inode cache lacks a compact per-inode RW mapping state"),
        ("inode_mapping_guards[FS_ICACHE_SIZE]",
         "mapping guards are not indexed by inode-cache identity"),
        ("wait_queue_init(&inode_mapping_waiters,WAIT_REASON_FS_CLAIM)",
         "mapping guard waiters are not initialized"),
    ):
        require(fs_source, fragment, message)

    read_lock = function(fs_source, "inode_mapping_read_lock")
    require(read_lock, "guard->writer_waiters==0",
            "new readers can starve a queued mapping writer")
    require(read_lock, "guard->readers++",
            "mapping readers are not published atomically")
    read_unlock = function(fs_source, "inode_mapping_read_unlock")
    require(read_unlock, "guard->readers==0&&guard->writer_waiters!=0",
            "uncontended mapping reads still scan the scheduler queue")

    write_lock = function(fs_source, "inode_mapping_write_lock")
    for fragment, message in (
        ("guard->writer==0&&guard->readers==0",
         "mapping writer does not wait for active readers"),
        ("guard->writer_waiters++",
         "mapping writers do not establish writer preference"),
        ("wait_queue_sleep_irq_uninterruptible(&inode_mapping_waiters)",
         "cleanup mapping writers can abandon finalization"),
    ):
        require(write_lock, fragment, message)

    for name in ("readi_with_auth", "readi_device"):
        body = function(fs_source, name)
        ordered(
            body,
            ("inode_mapping_read_lock(ip)", "readi_atomic(",
             "inode_mapping_read_unlock(ip)"),
            f"{name} does not retain the mapping read guard through copy/release",
        )
        if "bio_fs_atomic_" in body:
            raise ContractError(f"{name} prevents cache-miss lazy admission")

    read_atomic = function(fs_source, "readi_atomic")
    ordered(
        read_atomic,
        ("inode_mapping_require(ip,0)", "bmap_read_batch(",
         "either_copyout", "brelse("),
        "read path can dereference a mapping outside its read guard",
    )

    bmap = function(fs_source, "bmap")
    require(bmap, "inode_mapping_require(ip,alloc!=0)",
            "bmap does not enforce read/write mapping ownership")
    require(
        bmap,
        "receipt->data_block=candidate;a[bn]=candidate;"
        "result=fs_write_metadata_block(bp)",
        "indirect mapping publishes before establishing a rollback receipt",
    )
    require(
        bmap,
        "bmap_abort_allocation(ip,bn+NDIRECT,receipt)",
        "indirect publish barrier failure drops its allocation receipt",
    )
    abort = function(fs_source, "bmap_abort_allocation")
    require(abort, "inode_mapping_require(ip,1)",
            "failed allocation can roll back a block outside the writer guard")
    require(abort, "ip->addrs[bn]!=receipt->data_block",
            "allocation rollback is inferred without an allocation receipt")

    charged = function(fs_source, "writei_charged_locked")
    require(charged, "inode_mapping_require(ip,1)",
            "write implementation does not require the mapping writer")
    require(charged, "structbmap_allocation_receiptallocation={0}",
            "write allocation does not retain a rollback receipt")
    require(charged, "bmap_abort_allocation(ip,bn,&allocation)",
            "failed write does not consume its allocation receipt")
    charged_wrapper = function(fs_source, "writei_charged")
    ordered(
        charged_wrapper,
        ("inode_mapping_write_lock(ip,0)", "writei_charged_locked(",
         "inode_mapping_write_unlock(ip)"),
        "direct directory writes bypass the inode writer guard",
    )
    writei = function(fs_source, "writei_with_auth")
    ordered(
        writei,
        ("inode_mapping_write_lock(ip,0)", "bio_fs_atomic_enter()",
         "writei_charged_locked(", "bio_fs_atomic_leave()",
         "inode_mapping_write_unlock(ip)"),
        "ordinary writes acquire the mapping guard after BIO or release it early",
    )
    require(
        function(fs_source, "writei"),
        "returnwritei_with_auth(ip,cred,0,user_src,src,off,n)",
        "ordinary write wrapper bypasses the guarded implementation",
    )

    truncate = function(fs_source, "itruncate_detach")
    ordered(
        truncate,
        ("inode_mapping_write_lock(ip,0)", "itruncate_detach_all(",
         "inode_mapping_write_unlock(ip)"),
        "truncate does not publish detached mappings under the writer guard",
    )
    remove = function(fs_source, "inode_remove_detach")
    require(remove, "inode_mapping_write_lock(ip,1)",
            "final inode detach lacks an uninterruptible mapping writer")
    first_detach = remove.find("itruncate_detach_all(ip,reclaim)")
    first_unlock = remove.find("inode_mapping_write_unlock(ip)", first_detach)
    first_drop = remove.find("inode_cache_drop_ref(ip)", first_unlock)
    if min(first_detach, first_unlock, first_drop) < 0 or not (
        first_detach < first_unlock < first_drop
    ):
        raise ContractError("inode cache identity is recycled while writer-owned")

    scan = function(fs_source, "dir_scan_fill")
    scan_lock = scan.find("inode_mapping_read_lock(dp)")
    scan_map = scan.find("bmap(dp,", scan_lock)
    scan_release = scan.find("brelse(bp)", scan_map)
    scan_unlock = scan.find("inode_mapping_read_unlock(dp)", scan_lock)
    if min(scan_lock, scan_map, scan_release, scan_unlock) < 0 or not (
        scan_lock < scan_map < scan_release < scan_unlock
    ):
        raise ContractError(
            "directory scan releases mapping protection before its buffer"
        )

    for fragment, message in (
        ("ucharoffset_busy[FILEPOOLSIZE]",
         "open-file offsets lack per-object busy state"),
        ("uint16offset_waiters[FILEPOOLSIZE]",
         "open-file offset waiters are not tied to slot identity"),
        ("structwait_queueoffset_wait_queue",
         "open-file offset contention lacks a wait queue"),
    ):
        require(file_source, fragment, message)
    offset_lock = function(file_source, "file_offset_lock")
    require(offset_lock, "offset_busy[index]=1",
            "offset guard is not acquired atomically")
    require(offset_lock, "wait_queue_sleep_irq(",
            "contended offsets spin instead of blocking")
    offset_unlock = function(file_source, "file_offset_unlock")
    require(offset_unlock, "wait_queue_wake_all(",
            "shared offset wait queue can strand a slot waiter")
    require(offset_unlock,
            "filepool_allocator.offset_waiters[index]!=0",
            "uncontended offset updates still scan the scheduler queue")

    for name in ("inodewrite", "inoderead"):
        body = function(file_source, name)
        lock = body.find("file_offset_lock(f)")
        first_offset = body.find("f->off", lock)
        last_offset = body.rfind("f->off")
        unlock = body.rfind("file_offset_unlock(f)")
        if min(lock, first_offset, last_offset, unlock) < 0 or not (
            lock < first_offset <= last_offset < unlock
        ):
            raise ContractError(
                f"{name} does not serialize the complete shared-offset update"
            )

    push = function(file_source, "filepool_push_locked")
    require(push, "offset_busy[index]!=0||"
                  "filepool_allocator.offset_waiters[index]!=0",
            "file slot can be recycled while offset users remain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"inode mapping guard check failed: {error}", file=sys.stderr)
        return 1
    print("inode mapping guard check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
