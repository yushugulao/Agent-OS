#!/usr/bin/env python3
"""无需启动 Guest，检查有界工作流 VFS 作用域注册表。"""

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
        found = source.find(fragment, cursor + 1)
        if found < 0:
            raise ContractError(message)
        cursor = found


def check(root: Path) -> None:
    source = compact(root / "os/vfs_security.c")
    header = compact(root / "os/vfs_security.h")

    reject(source, "NPROC", "VFS scope registry still depends on the process table")
    reject(source, "vfs_scope_refs", "legacy flat scope registry remains")
    for fragment, label in (
        (
            "structvfs_scope_refrefs[VFS_SCOPE_LIFECYCLE_CAP];",
            "lifecycle-bounded record storage",
        ),
        (
            "uinthash_heads[VFS_SCOPE_LIFECYCLE_CAP];",
            "scope-id hash buckets",
        ),
        ("uintfree_head;", "constant-time free list"),
        ("uintretiring_head;", "retiring worklist head"),
        ("uintretiring_tail;", "retiring worklist tail"),
        ("uintretiring_cursor;", "fair retiring cursor"),
        ("uintused_count;", "used count"),
        ("uintactive_count;", "active count"),
        ("uintretiring_count;", "retiring count"),
        ("uintfree_count;", "free count"),
    ):
        require(source, fragment, f"scope registry lacks {label}")
    require(
        header,
        "#defineVFS_SCOPE_LIFECYCLE_CAPWORKFLOW_LIFECYCLE_CAP",
        "scope registry is not bounded by the lifecycle capacity",
    )

    lookup = function(source, "vfs_scope_find_locked")
    for fragment, message in (
        ("hash_heads[vfs_scope_hash(scope_id)]", "lookup bypasses the scope hash"),
        (
            "visited>=VFS_SCOPE_LIFECYCLE_CAP",
            "hash collision walk lacks a lifecycle-cap bound",
        ),
        ("ref->scope_id==scope_id", "lookup does not match the trusted scope id"),
    ):
        require(lookup, fragment, message)

    insert = function(source, "vfs_scope_registry_insert_locked")
    for fragment, message in (
        ("registry->free_head", "scope creation scans instead of popping a free slot"),
        ("registry->hash_heads[bucket]=link", "scope creation is not indexed"),
        ("registry->free_count--", "scope creation does not charge free capacity"),
        ("registry->used_count++", "scope creation does not publish used capacity"),
        ("registry->active_count++", "scope creation does not publish active capacity"),
    ):
        require(insert, fragment, message)

    create = function(source, "vfs_scope_create")
    reject(create, "for(", "scope admission still scans a registry")
    for fragment, message in (
        ("vfs_scope_find_locked(scope_id)==0", "scope admission skips duplicate lookup"),
        ("registry->free_count>0", "scope admission ignores compact capacity"),
        (
            "registry->active_count+registry->retiring_count<"
            "VFS_SCOPE_MAX_ACTIVE",
            "scope admission ignores active and retiring counts",
        ),
        (
            "vfs_scope_registry_insert_locked(scope_id,created,storage)",
            "scope admission bypasses the canonical insertion path",
        ),
    ):
        require(create, fragment, message)

    retire_add = function(source, "vfs_scope_retiring_add_locked")
    retire_remove = function(source, "vfs_scope_retiring_remove_locked")
    for fragment in (
        "registry->retiring_tail=link",
        "registry->retiring_cursor=link",
        "registry->active_count--",
        "registry->retiring_count++",
    ):
        require(retire_add, fragment, "retiring insertion is incomplete")
    for fragment in (
        "registry->retiring_cursor",
        "registry->retiring_count--",
        "ref->retiring=0",
    ):
        require(retire_remove, fragment, "retiring removal is incomplete")

    reaper = function(source, "vfs_scope_reap_pending")
    reject(reaper, "for(", "reaper still scans the registry")
    require(
        reaper,
        "vfs_scope_retiring_next_locked()",
        "reaper bypasses the retiring worklist",
    )

    for name in (
        "vfs_scope_join",
        "vfs_scope_release",
        "vfs_scope_reclaim_advance",
        "vfs_scope_preserve_on_retire",
        "vfs_scope_active",
        "vfs_scope_retiring",
        "vfs_scope_retained",
        "vfs_scope_lifecycle",
        "vfs_scope_bind_controller",
        "vfs_scope_close_owned",
        "vfs_scope_close_trusted",
    ):
        body = function(source, name)
        require(body, "vfs_scope_find_locked(scope_id)", f"{name} bypasses indexed lookup")
        reject(body, "for(", f"{name} scans the scope registry")

    for name in (
        "vfs_scope_join",
        "vfs_scope_release",
        "vfs_scope_reclaim_advance",
        "vfs_scope_bind_controller",
        "vfs_scope_close_owned",
    ):
        require(
            function(source, name),
            "workflow_lifecycle_key_equal",
            f"{name} omits lifecycle generation validation",
        )

    guarantee = function(source, "vfs_scope_storage_guarantee")
    require(
        guarantee,
        "i<VFS_SCOPE_LIFECYCLE_CAP",
        "storage guarantee is not bounded by lifecycle capacity",
    )
    require(
        guarantee,
        "allocated=registry->active_count+registry->retiring_count",
        "storage guarantee does not preserve admission accounting",
    )
    reject(guarantee, "NPROC", "storage guarantee scans the process table")

    release = function(source, "vfs_scope_release")
    require_order(
        release,
        (
            "workflow_lifecycle_leave(lifecycle)",
            "vfs_scope_retiring_add_locked(matched)",
            "fs_storage_scope_account_close(storage)",
            "bio_scope_quiesce(scope_id)",
            "agent_background_request()",
        ),
        "scope teardown publication order changed",
    )
    reclaim = function(source, "vfs_scope_reclaim_complete")
    require_order(
        reclaim,
        (
            "agent_scope_reclaim_begin(scope_id,lifecycle,&target)",
            "fs_reclaim_scope_files(scope_id)",
            "agent_scope_reclaim_metadata_done(scope_id,lifecycle,metadata_target)",
            "bio_scope_retire(scope_id)",
            "vfs_scope_registry_remove_locked(ref)",
        ),
        "scope reclaim phase order changed",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"vfs scope registry check failed: {error}", file=sys.stderr)
        return 1
    print("vfs scope registry check passed: hashed lifecycle-bounded lookup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
