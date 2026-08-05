#!/usr/bin/env python3
"""Check that block-I/O admission starts at a real cache-miss boundary."""

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
    brace = text.find("{", match.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start() : index + 1]
    raise ValueError(f"unterminated function {name}")


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def check(root: Path) -> None:
    bio_h = compact(root / "os/bio.h")
    bio = compact(root / "os/bio.c")
    main = compact(root / "os/main.c")
    proc = compact(root / "os/proc.h")
    syscall = compact(root / "os/syscall.c")
    fs = compact(root / "os/fs.c")
    policy = compact(root / "io_policy.h")
    guest = compact(root / "user/src/iobudget_ucore.c")

    for fragment in (
        "intbio_request_begin_current_lazy(void);",
        "intbio_request_upgrade_current(void);",
        "intbio_request_active_current(void);",
        "uint64lazy_started;",
        "uint64upgraded;",
        "uint64cache_only;",
    ):
        require(bio_h, fragment, "lazy BIO public contract is incomplete")
    require(proc, "uintio_request_flags;uint64io_request_id;",
            "lazy request state does not use the thread alignment hole")
    for fragment in (
        "unsignedlonglonglazy_started;",
        "unsignedlonglongupgraded;",
        "unsignedlonglongcache_only;",
    ):
        require(policy, fragment, "lazy BIO counters are not observable")

    begin = function(bio, "bio_request_begin_current_lazy_mode")
    for forbidden in (
        "io_wait_until_admitted(",
        "io_active_request_acquire(",
        "resource_rate_reserve_many(",
    ):
        reject(begin, forbidden, "lazy begin still reserves I/O capacity")
    require(begin, "thread->io_request_flags=BIO_REQUEST_LAZY|",
            "lazy begin does not publish a lightweight identity")
    require(begin, "state->lazy_started++;io_policy.lazy_started++;",
            "lazy begin counters are incomplete")

    upgrade = function(bio, "bio_request_upgrade_current_mode")
    for fragment in (
        "thread->bio_buffer_holds!=0||thread->bio_fs_atomic_depth!=0",
        "io_active_request_acquire(state);",
        "result=io_wait_until_admitted(",
        "state->retiring||state->quiesced",
        "thread->io_request_flags|=BIO_REQUEST_ACTIVE;",
        "state->upgraded++;io_policy.upgraded++;",
    ):
        require(upgrade, fragment, "lazy upgrade is not atomic and fail closed")
    if upgrade.find("io_active_request_acquire(state);") > upgrade.find(
        "io_wait_until_admitted("
    ):
        raise ValueError("lazy upgrade does not pin its owner before sleeping")

    end = function(bio, "bio_request_end_current_mode")
    inactive = end.find("if((flags&BIO_REQUEST_ACTIVE)==0)")
    cache_only = end.find("io_policy.cache_only++", inactive)
    refund = end.find("io_rate_lease_refund", inactive)
    if inactive < 0 or cache_only < 0 or (refund >= 0 and refund < cache_only):
        raise ValueError("cache-only end is not an O(1) no-refund path")

    bget = function(bio, "bget")
    upgrade_at = bget.find("bio_request_upgrade_current()")
    acquire_at = bget.find("b->refcnt++")
    if upgrade_at < 0 or acquire_at < 0 or upgrade_at > acquire_at:
        raise ValueError("bget upgrades after acquiring a buffer")
    require(bget, "if(b==0||!b->valid)",
            "bget does not distinguish valid cache hits")
    require(bget, "gotoretry;", "bget does not recheck a cache-fill race")

    batch = function(bio, "bread_batch")
    preflight = batch.find("bio_cache_batch_preflight(dev,blocknos,count)")
    first_get = batch.find("bget(")
    if preflight < 0 or first_get < 0 or preflight > first_get:
        raise ValueError("batch read can hold a prefix before admission")
    batch_probe = function(bio, "bio_cache_batch_preflight")
    require(batch_probe, "for(uinti=0;i<count;i++)",
            "batch preflight does not inspect the entire request")
    require(batch_probe, "b==0||!b->valid||",
            "batch preflight ignores invalid cache lines")

    transaction = function(syscall, "syscall_transaction_begin")
    require(transaction,
            "transaction->fs_epoch_admitted?bio_request_begin_current():"
            "bio_request_begin_current_lazy();",
            "cache-only syscalls do not select lazy admission")
    for name in ("readi_with_auth", "readi_device", "dir_scan_fill"):
        body = function(fs, name)
        reject(body, "bio_fs_atomic_enter()",
               f"{name} keeps an unnecessary no-sleep marker")
        reject(body, "bio_fs_atomic_leave()",
               f"{name} keeps an unnecessary no-sleep marker")
    write = function(bio, "bwrite")
    require(write, "if(!bio_request_active_current())"
                   "returnVIRTIO_DISK_ERR_BUSY;",
            "physical writes can bypass lazy admission")
    boot = function(main, "main")
    boot_steps = tuple(
        boot.find(fragment)
        for fragment in (
            "load_init_app();",
            "show_all_files();",
            "bio_policy_start();",
            "virtio_disk_runtime_start();",
            "scheduler();",
        )
    )
    if any(position < 0 for position in boot_steps) or tuple(sorted(boot_steps)) != boot_steps:
        raise ValueError("runtime I/O admission starts before polling-only boot I/O completes")
    guest_probe = function(guest, "check_lazy_cache_admission")
    for fragment in (
        "after.lazy_started-before.lazy_started==2*LAZY_CACHE_ROUNDS",
        "after.cache_only-before.cache_only==2*LAZY_CACHE_ROUNDS",
        "after.upgraded==before.upgraded",
        "after.leased==before.leased",
    ):
        require(guest_probe, fragment,
                "Guest regression does not prove the cache-only fast path")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"lazy BIO admission check failed: {error}", file=sys.stderr)
        return 1
    print("lazy BIO admission check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
