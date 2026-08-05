#!/usr/bin/env python3
"""Check the bounded inode sequential-read batching contract."""

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


def check(root: Path) -> None:
    fs = compact(root / "os/fs.c")
    virtio = compact(root / "os/virtio.h")
    disk = compact(root / "os/virtio_disk.c")
    performance = compact(root / "os/performance_stats.h")

    require(fs, "#defineFS_READ_BATCH_MAX4U", "read batch is not stack-bounded to four blocks")
    require(
        fs,
        "_Static_assert(FS_READ_BATCH_MAX<=VIRTIO_DISK_READ_BATCH_MAX,",
        "filesystem batch width is not tied to the device contract",
    )
    require(
        fs,
        "_Static_assert(FS_READ_BATCH_MAX<=VM_COPY_SEGMENT_MAX&&"
        "FS_READ_BATCH_MAX*BSIZE<=VM_COPYOUTV_MAX_BYTES,",
        "filesystem batch does not fit the bounded VM copy window",
    )
    require(
        virtio,
        "#defineVIRTIO_DISK_READ_BATCH_MAXNUM",
        "VirtIO read batching is not queue-width bounded",
    )
    for symbol in (
        "KERNEL_PERFORMANCE_VIRTIO_SINGLE=0",
        "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH=1",
        "KERNEL_PERFORMANCE_VIRTIO_READ_BATCH=2",
    ):
        require(performance, symbol, "VirtIO submission telemetry lacks distinct modes")

    submissions = (
        (
            "disk_submit",
            "kernel_performance_virtio_notify(1,KERNEL_PERFORMANCE_VIRTIO_SINGLE,0);",
            "single-request telemetry is not mode-neutral",
        ),
        (
            "disk_submit_write_pair",
            "kernel_performance_virtio_notify(2,KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH,0);",
            "direct write-pair telemetry is not classified as a batch",
        ),
        (
            "disk_submit_indirect",
            "kernel_performance_virtio_notify(count,type==VIRTIO_BLK_T_OUT?"
            "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH:"
            "KERNEL_PERFORMANCE_VIRTIO_READ_BATCH,1);",
            "indirect read batches are not visible in performance evidence",
        ),
    )
    for name, fragment, message in submissions:
        submission = function(disk, name)
        require(submission, fragment, message)
        if submission.count("kernel_performance_virtio_notify(") != 1:
            raise ValueError(f"{name} must record exactly one queue notification")

    wrapper = function(fs, "fs_read_blocks_batch")
    require(
        wrapper,
        "result=bread_batch(dev,blocknos,out,count);",
        "filesystem batch wrapper bypasses the buffer-cache batch API",
    )
    require(
        wrapper,
        "result==VIRTIO_DISK_ERR_BUSY?FS_FAILURE_SCHEDULING_UNAVAILABLE:FS_FAILURE_TRANSIENT_READ",
        "batch read errors do not preserve filesystem failure classes",
    )

    mapper = function(fs, "bmap_read_batch")
    for fragment, message in (
        (
            "while(mapped<count&&bn+mapped<NDIRECT)",
            "read mapper does not gather direct blocks as one bounded operation",
        ),
        (
            "result=fs_read_block(ip->dev,ip->addrs[NDIRECT],&bp);",
            "read mapper does not acquire the indirect map once",
        ),
        (
            "while(mapped<count&&index<NINDIRECT)",
            "read mapper does not gather adjacent indirect entries",
        ),
        ("brelse(bp);", "read mapper leaks its indirect-map buffer"),
    ):
        require(mapper, fragment, message)
    if mapper.count("fs_read_block(") != 1:
        raise ValueError("read mapper reacquires the indirect map per data block")

    body = function(fs, "readi_atomic")
    for fragment, message in (
        ("uintblocknos[FS_READ_BATCH_MAX];", "block-number workspace is not bounded"),
        ("structbuf*buffers[FS_READ_BATCH_MAX];", "buffer workspace is not bounded"),
        (
            "structvm_copy_segmentsegments[FS_READ_BATCH_MAX];",
            "scatter-copy workspace is not bounded by the read batch",
        ),
        (
            "batch_limit=device_read?1U:FS_READ_BATCH_MAX;",
            "forced device reads can enter the cache batch path",
        ),
        (
            "batch_count=MIN(batch_count,batch_limit);",
            "sequential mapping is not bounded by the stack batch width",
        ),
        (
            "mapped=bmap_read_batch(ip,off/BSIZE,batch_count,blocknos,&map_result);",
            "read path bypasses the read-only batch mapper",
        ),
        (
            "failure_result=fs_read_blocks_batch(ip->dev,blocknos,buffers,batch_count);",
            "multi-block reads bypass the filesystem batch wrapper",
        ),
        (
            "failure_result=fs_read_block(ip->dev,blocknos[0],&buffers[0]);",
            "failed batches cannot recover the original positive-prefix semantics",
        ),
        (
            "failure_result=fs_read_device_block(ip->dev,blocknos[0],&buffers[0]);",
            "forced device reads no longer use the direct device path",
        ),
        (
            "batch_count==1&&either_copyout(user_dst,dst,(char*)segments[0].source,"
            "segments[0].length)<0",
            "single-block reads lost their low-overhead checked copyout path",
        ),
        (
            "batch_count>1&&either_copyoutv(user_dst,dst,segments,batch_count)<0",
            "multi-block reads bypass the bounded scatter copyout window",
        ),
        ("brelse(buffers[copied]);", "batch buffers are not released"),
        ("checkpoint=bio_request_checkpoint();", "read batches lack a boundary checkpoint"),
        (
            "while(copied<batch_count)",
            "batch buffers are not released on copyout failure",
        ),
    ):
        require(body, fragment, message)

    if "bmap(" in body:
        raise ValueError("read path regressed to the allocation-capable per-block mapper")
    map_position = body.find("mapped=bmap_read_batch(")
    submit_position = body.find("failure_result=fs_read_blocks_batch(")
    vector_position = body.find("batch_bytes=0;")
    copy_position = body.find("either_copyoutv(")
    release_position = body.find("brelse(buffers[copied]);", copy_position)
    advance_position = body.find("tot+=batch_bytes;", release_position)
    checkpoint_position = body.find("checkpoint=bio_request_checkpoint();")
    if min(
        map_position,
        submit_position,
        vector_position,
        copy_position,
        release_position,
        advance_position,
        checkpoint_position,
    ) < 0:
        raise ValueError("read batch ordering contract is incomplete")
    if not (
        map_position
        < submit_position
        < vector_position
        < copy_position
        < release_position
        < advance_position
        < checkpoint_position
    ):
        raise ValueError(
            "read batch must map, submit, prepare, copy, release, commit, then checkpoint"
        )
    if body.count("either_copyoutv(") != 1:
        raise ValueError("multi-block batches must use one scatter copyout")
    if body.count("bio_request_checkpoint()") != 1:
        raise ValueError("read path must checkpoint once per completed batch")

    fallback = body.find(
        "failure_result=fs_read_block(ip->dev,blocknos[0],&buffers[0]);",
        submit_position,
    )
    if fallback < submit_position or fallback > copy_position:
        raise ValueError("batch failure fallback does not precede copyout")
    require(
        body,
        "batch_count=1;mapping_failed=0;failure_result=fs_read_block("
        "ip->dev,blocknos[0],&buffers[0]);",
        "failed batch fallback can discard a mapped positive prefix",
    )
    require(
        body,
        "batch_bytes=0;for(copied=0;copied<batch_count;copied++){"
        "uintblock_offset=copied==0?off%BSIZE:0;"
        "m=MIN(n-tot-batch_bytes,BSIZE-block_offset);"
        "segments[copied].source=(char*)buffers[copied]->data+block_offset;"
        "segments[copied].length=m;batch_bytes+=m;}",
        "read batch does not build one bounded segment per acquired buffer",
    )
    require(
        body,
        "copied=0;while(copied<batch_count){if(buffers[copied]!=0)"
        "brelse(buffers[copied]);copied++;}if(failed)break;"
        "tot+=batch_bytes;off+=batch_bytes;dst+=batch_bytes;"
        "checkpoint=bio_request_checkpoint();",
        "copyout failure can commit bytes or leak batch buffers",
    )
    require(
        body,
        "if(mapping_failed){failure_result=map_result;failed=1;break;}",
        "a sparse mapping after a copied prefix is silently skipped",
    )
    require(
        body,
        "if(failed&&tot==0)returnfailure_result;returntot;",
        "read result no longer distinguishes zero-progress failure from a prefix",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"sequential read batch check failed: {error}", file=sys.stderr)
        return 1
    print("sequential read batch check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
