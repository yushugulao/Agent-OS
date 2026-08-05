#!/usr/bin/env python3
"""Check the bounded scatter-copyout VM snapshot contract."""

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
    header = compact(root / "os/vm.h")
    source = compact(root / "os/vm.c")

    for fragment, message in (
        ("#defineVM_COPY_SEGMENT_MAX4U", "scatter segment bound is not four"),
        ("#defineVM_COPYOUTV_MAX_BYTESPGSIZE", "scatter byte bound is not one page"),
        ("#defineVM_COPYOUTV_MAX_USER_PAGES2U", "scatter user page bound is not two"),
        (
            "structvm_copy_segment{constchar*source;uint64length;};",
            "scatter segment ABI is missing",
        ),
        (
            "intcopyoutv(pagetable_t,uint64,conststructvm_copy_segment*,uint);",
            "copyoutv API is not exported",
        ),
        (
            "inteither_copyoutv(int,uint64,conststructvm_copy_segment*,uint);",
            "either_copyoutv API is not exported",
        ),
    ):
        require(header, fragment, message)

    total = function(source, "vm_copy_segments_total")
    for fragment, message in (
        ("count>VM_COPY_SEGMENT_MAX", "segment count is not bounded"),
        ("count!=0&&segments==0", "non-empty scatter accepts a null vector"),
        (
            "source==0||length>(uint64)-1-source",
            "source address overflow is not rejected",
        ),
        (
            "length>VM_COPYOUTV_MAX_BYTES-total",
            "scatter total is not overflow-safe and byte-bounded",
        ),
        ("*total_out=total;", "validated total is not published once"),
    ):
        require(total, fragment, message)

    body = function(source, "copyoutv")
    for fragment, message in (
        (
            "pte_t*leaves[VM_COPYOUTV_MAX_USER_PAGES];",
            "copyoutv physical-page workspace is not bounded",
        ),
        (
            "vm_copy_segments_total(segments,count,&total)",
            "copyoutv bypasses complete vector validation",
        ),
        (
            "pagetable==0||dstva>=MAXVA||total>MAXVA-dstva",
            "copyoutv destination overflow is not rejected",
        ),
        (
            "page_count>VM_COPYOUTV_MAX_USER_PAGES",
            "copyoutv can exceed its two-page workspace",
        ),
        (
            "p==0||p->pagetable!=pagetable||proc_vm_snapshot_begin(p)<0",
            "copyoutv is not bound to one current-process page table snapshot",
        ),
        (
            "for(uinti=0;i<page_count;i++)",
            "copyoutv does not prepare the complete destination range first",
        ),
        (
            "leaves[i]=pte;}for(uinti=0;i<page_count;i++){uint64page=",
            "copyoutv does not validate the complete range before COW",
        ),
        (
            "pte_t*pte=leaves[i];if((*pte&PTE_COW)!=0){"
            "if(uvm_cow_fault(pagetable,page)<0)",
            "copyoutv does not promote every COW page before writing",
        ),
        (
            "PTE2PA(*leaves[page_index])+offset",
            "copyoutv does not reuse its prepared leaf pages",
        ),
        (
            "for(uinti=0;i<count;i++){constchar*source=segments[i].source;",
            "copyoutv does not consume the validated source vector",
        ),
        ("proc_vm_snapshot_end(p);", "copyoutv leaks its VM snapshot"),
    ):
        require(body, fragment, message)

    prepare = body.find("for(uinti=0;i<page_count;i++)")
    prepared = body.find("leaves[i]=pte;")
    promoted = body.find("pte_t*pte=leaves[i];")
    copy_loop = body.find("for(uinti=0;i<count;i++)")
    write = body.find("memmove(")
    snapshot_begin = body.find("proc_vm_snapshot_begin(p)")
    snapshot_end = body.find("proc_vm_snapshot_end(p)")
    if min(prepare, prepared, promoted, copy_loop, write, snapshot_begin, snapshot_end) < 0:
        raise ValueError("copyoutv validation/prepare/write ordering is incomplete")
    if not snapshot_begin < prepare < prepared < promoted < copy_loop < write < snapshot_end:
        raise ValueError("copyoutv can write before all pages are prepared")
    if body.count("walk_user_leaf(") != 1:
        raise ValueError("copyoutv repeats PTE walks per source segment")
    for forbidden in (
        "user_range_check(",
        "copyout(",
        "bio_",
        "kernel_work_checkpoint",
        "wait_queue",
        "sleep(",
        "sched(",
    ):
        reject(body, forbidden, "copyoutv snapshot crosses repeated validation or scheduling")

    kernel = function(source, "copyoutv_kernel")
    for fragment, message in (
        (
            "vm_copy_segments_total(segments,count,&total)",
            "kernel scatter path bypasses vector validation",
        ),
        (
            "dst==0||total>(uint64)-1-dst",
            "kernel scatter destination overflow is not rejected",
        ),
        ("memmove((void*)cursor,segments[i].source,", "kernel scatter path is missing"),
    ):
        require(kernel, fragment, message)
    if kernel.find("vm_copy_segments_total(") > kernel.find("memmove("):
        raise ValueError("kernel scatter writes before validating every segment")

    either = function(source, "either_copyoutv")
    require(
        either,
        "returncopyoutv_kernel(dst,segments,count);",
        "kernel scatter path bypasses its bounded helper",
    )
    require(
        either,
        "returncopyoutv(p->pagetable,dst,segments,count);",
        "user scatter path bypasses copyoutv",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"copyoutv window check failed: {error}", file=sys.stderr)
        return 1
    print("copyoutv window check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
