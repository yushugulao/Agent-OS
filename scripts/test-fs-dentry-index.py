#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(text: str) -> str:
    return " ".join(text.split())


def function_body(source: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^.*\b{re.escape(name)}\s*\(")
    for match in pattern.finditer(source):
        opening = source.find("{", match.end())
        semicolon = source.find(";", match.end(), opening if opening >= 0 else None)
        if opening < 0 or semicolon >= 0:
            continue
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[opening : index + 1]
    raise ContractError(f"missing function: {name}")


def require(text: str, token: str, context: str) -> None:
    if token not in compact(text):
        raise ContractError(f"{context} missing: {token}")


def validate(source: str) -> None:
    require(source, "#define FS_DENTRY_INDEX_CAP 512U", "bounded dentry table")
    require(
        source,
        "#define FS_DIRECTORY_INDEX_MAX_ENTRIES NINODE",
        "bounded occupancy table",
    )
    require(
        source,
        "sizeof(fs_dentry_index) + sizeof(fs_directory_indexes) <= 16U * 1024U",
        "dentry BSS budget",
    )
    entry = re.search(
        r"struct fs_dentry_index_entry\s*\{(?P<body>.*?)\};", source, re.S
    )
    if entry is None:
        raise ContractError("missing dentry entry type")
    for forbidden in ("target_policy", "target_scope_id", "target_exec_profile", "name["):
        if forbidden in entry.group("body"):
            raise ContractError(f"derived entry retains authoritative field: {forbidden}")

    first_empty = function_body(source, "fs_directory_index_first_empty")
    if re.search(r"\b(for|while)\s*\(", first_empty):
        raise ContractError("warm free-slot lookup is not O(1)")
    require(first_empty, "state->first_free_entry", "free-slot hint")

    scan = function_body(source, "dir_scan_fill")
    require(
        scan,
        "kernel_performance_directory_probe(batch->scanned);",
        "directory probe measurement",
    )
    validate_hit = function_body(source, "fs_dentry_index_validate")
    for token in (
        "readi(dp, &kernel_cred",
        "de.inum != entry->target_inum",
        "fs_dentry_hash(dp, actual) != entry->hash",
        "vfs_inode_label_valid(target)",
        "target->vfs_incarnation != entry->target_incarnation",
        "kernel_performance_directory_probe(1);",
    ):
        require(validate_hit, token, "authoritative hit validation")
    if "fs_dentry_gate_lock" in validate_hit or "fs_dentry_gate_require" in validate_hit:
        raise ContractError("warm hit validation holds the derived-index gate across I/O")

    lookup = compact(function_body(source, "dirlookup"))
    copy_at = lookup.find("fs_dentry_index_snapshot_next(")
    unlock_at = lookup.find("fs_dentry_gate_unlock();", copy_at)
    validate_at = lookup.find("fs_dentry_index_validate(", copy_at)
    if copy_at < 0 or unlock_at < copy_at or validate_at < unlock_at:
        raise ContractError("dirlookup does not copy then unlock before validation")
    for token in (
        "fs_dentry_index_snapshot_stable(index_generation)",
        "fs_dentry_index_invalidate_snapshot(",
        "goto authoritative;",
    ):
        if token not in lookup:
            raise ContractError(f"dirlookup snapshot contract missing: {token}")

    link_wrapper = compact(function_body(source, "dirlink"))
    require(
        link_wrapper,
        "return dirlink_impl(dp, name, inum, cred, 0);",
        "ordinary dirlink wrapper",
    )
    publish_wrapper = compact(function_body(source, "dirlink_publish"))
    require(
        publish_wrapper,
        "return dirlink_impl(dp, name, inum, cred, 1);",
        "publish dirlink wrapper",
    )
    link = compact(function_body(source, "dirlink_impl"))
    for token in (
        "fs_namespace_gate_lock()",
        "fs_dentry_index_create_conflict(",
        "fs_directory_index_first_empty(state)",
        "writei_charged(dp, &kernel_cred",
        "fs_dentry_index_publish_link(dp, key",
        "fs_namespace_gate_unlock();",
    ):
        if token not in link and token != "fs_directory_index_first_empty(state)":
            raise ContractError(f"dirlink fast path missing: {token}")
    if link.find("fs_dentry_index_publish_link(") > link.find(
        "fs_durable_barrier_forward()"
    ):
        raise ContractError("dirlink publishes derived state after its return barrier")

    conflict = function_body(source, "fs_dentry_index_create_conflict")
    require(conflict, "fs_directory_index_first_empty(state)", "indexed create free slot")
    require(conflict, "fs_dentry_index_validate(", "indexed create revalidation")

    unlink = compact(function_body(source, "dirunlink"))
    for token in (
        "fs_namespace_gate_lock()",
        "writei_charged(dp, &kernel_cred",
        "fs_dentry_index_publish_unlink(dp, key, offset, expected_inum",
        "fs_namespace_gate_unlock();",
    ):
        if token not in unlink:
            raise ContractError(f"dirunlink closure missing: {token}")
    remove = function_body(source, "fs_dentry_index_publish_unlink")
    for token in (
        "entry->state = FS_DENTRY_INDEX_TOMBSTONE;",
        "fs_directory_index_occupied_set(state, offset, 0);",
        "state->entries--;",
        "fs_dentry_index_bump();",
    ):
        require(remove, token, "unlink derived-state update")

    writei = function_body(source, "writei_with_auth")
    require(writei, "ip->type == T_DIR", "generic directory mutation")
    require(
        writei,
        "fs_dentry_index_invalidate_directory(ip);",
        "generic directory invalidation",
    )
    require(source, "fs_dentry_boot_token", "boot-safe dentry gate")
    require(source, "state->overflow ? 0 : 1", "overflow authoritative fallback")
    for gate in ("fs_dentry_waiters", "fs_namespace_waiters"):
        require(
            source,
            f"wait_queue_wake_all(&{gate});",
            "cancellable gate release",
        )
        if f"wait_queue_wake_one(&{gate});" in source:
            raise ContractError(f"{gate} retains single-waiter handoff")


def mutation_self_test(source: str) -> None:
    mutations = (
        "target->vfs_incarnation != entry->target_incarnation",
        "fs_dentry_index_publish_unlink(dp, key, offset, expected_inum",
        "fs_directory_index_occupied_set(state, offset, 0);",
        "kernel_performance_directory_probe(1);",
        "fs_namespace_gate_lock()",
        "wait_queue_wake_all(&fs_dentry_waiters);",
        "wait_queue_wake_all(&fs_namespace_waiters);",
    )
    for token in mutations:
        if token not in source:
            raise ContractError(f"mutation anchor missing: {token}")
        mutated = (
            source.replace(token, "")
            if token == "fs_namespace_gate_lock()"
            else source.replace(token, "", 1)
        )
        try:
            validate(mutated)
        except ContractError:
            continue
        raise ContractError(f"mutation escaped dentry contract: {token}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = (root / "os" / "fs.c").read_text(encoding="utf-8")
    validate(source)
    mutation_self_test(source)
    print("fs-dentry-index: passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as error:
        print(f"fs-dentry-index: {error}", file=sys.stderr)
        raise SystemExit(1)
