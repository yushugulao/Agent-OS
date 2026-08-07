#!/usr/bin/env python3
"""验证稀疏 Sv39 fork 与拆除遍历不变量。"""

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
    vm = compact(root / "os/vm.c")
    release = function(vm, "uvm_release_range_tree")
    free = function(vm, "uvmfree")
    cleanup = function(vm, "uvmfree_cleanup")
    clone = function(vm, "uvmcopy_clone_tree")
    commit = function(vm, "uvmcopy_commit_parent")
    copy = function(vm, "uvmcopy")

    for body, label in ((free, "uvmfree"), (cleanup, "cleanup teardown")):
        require(
            body,
            "uvm_release_range_tree(pagetable,max_page*PGSIZE,",
            f"{label} does not use the allocated page-table tree",
        )
        reject(
            body,
            "uvmunmap(pagetable,0,max_page",
            f"{label} regressed to a dense max_page scan",
        )
    for fragment, message in (
        ("l2_span=1ULL<<PXSHIFT(2)",
         "teardown does not skip absent Sv39 subtrees"),
        ("l1_span=1ULL<<PXSHIFT(1)",
         "teardown does not traverse the Sv39 middle level"),
        ("va>=limit", "teardown can cross the user range"),
        ("krelease_account_page((void*)PTE2PA(leaf))",
         "teardown does not release mapped leaf ownership"),
        ("uvm_page_free(root,l0)",
         "teardown leaves empty page-table pages charged"),
        ("uvm_page_free(root,l1)",
         "teardown leaves empty middle page-table pages charged"),
        ("kernel_work_checkpoint_cleanup(KERNEL_WORK_PAGE_UNITS)",
         "teardown tree traversal lost cleanup fairness"),
    ):
        require(release, fragment, message)

    for fragment, message in (
        ("source=old_l0[l0_slot]", "fork does not traverse source PTEs directly"),
        ("uvm_page_alloc(state->new_root)",
         "fork does not charge cloned page-table nodes"),
        ("kretain_account_page((void*)PTE2PA(source))",
         "fork does not retain shared leaf ownership"),
        ("new_l0[l0_slot]=PA2PTE(PTE2PA(source))|flags",
         "fork does not install the validated leaf directly"),
        ("uvmcopy_leaf_flags_valid(flags)",
         "fork bypasses leaf permission validation"),
        ("kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS)",
         "fork tree traversal lost its work budget"),
    ):
        require(clone, fragment, message)
    reject(clone, "walk(", "fork clone regressed to per-page page-table walks")
    reject(clone, "mappages(", "fork clone regressed to per-page mapping calls")
    reject(clone, "parent_l0[l0_slot]=", "fork mutates the parent before commit")

    require(commit, "va>=limit", "fork commit can cross copy limit")
    require(
        commit,
        "parent_l0[l0_slot]=(parent&~PTE_W)|PTE_COW",
        "fork commit does not harden writable parent leaves",
    )
    require(
        commit,
        "PTE2PA(parent)!=PTE2PA(child)",
        "fork commit does not bind parent and child leaf identity",
    )
    require(
        copy,
        "if((new[i]&PTE_V)!=0)return-1",
        "fork does not reserve an empty destination range",
    )
    clone_at = copy.find("uvmcopy_clone_tree(&state,old,new,limit)")
    commit_at = copy.find("uvmcopy_commit_parent(old,new,limit)")
    if commit_at < 0 or clone_at < 0 or commit_at < clone_at:
        raise ContractError(
            "fork publication order no longer preserves failure atomicity"
        )
    ordered(
        copy,
        (
            "uvmcopy_clone_tree(&state,old,new,limit)",
            "uvm_release_range_tree(new,limit,1)",
            "uvmcopy_commit_parent(old,new,limit)",
            "sfence_vma()",
        ),
        "fork publication order no longer preserves failure atomicity",
    )
    reject(copy, "for(page=0;page<max_page", "fork regressed to a dense scan")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"vm page-table fastpath check: {error}", file=sys.stderr)
        return 1
    print("vm page-table fastpath check: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
