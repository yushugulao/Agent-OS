#!/usr/bin/env python3
"""检查固定 bank、生命周期作用域内的 Agent 文件代际索引。"""

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
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def require_count(source: str, fragment: str, count: int, message: str) -> None:
    if source.count(fragment) != count:
        raise ContractError(message)


def check(root: Path) -> None:
    source = compact(root / "os/agent_file_state.c")
    state_header = compact(root / "os/agent_file_state_internal.h")
    catalog = compact(root / "os/agent_metadata_catalog.c")
    vfs = compact(root / "os/vfs_security.c")

    reject(source, "NPROC", "file generation index still depends on the process table")
    reject(source, "slot=lifecycle.id", "lifecycle ids are still used as bank indexes")
    reject(source, "file_version_probe_locked", "file versions still use open addressing")
    reject(source, "file_version_hash", "file versions still use a global hash table")
    for fragment, message in (
        (
            "#defineAGENT_FILE_CACHE_SYSTEM_SLOT0U",
            "SYSTEM does not own the dedicated bank zero",
        ),
        (
            "#defineAGENT_FILE_CACHE_SCOPE_MAX(VFS_SCOPE_MAX_ACTIVE+1U)",
            "file version banks are not bounded by admitted workflows plus SYSTEM",
        ),
        (
            "AGENT_FILE_VERSION_SYSTEM_RESIDENT+"
            "VFS_SCOPE_MAX_ACTIVE*AGENT_FILE_VERSION_SCOPE_RESIDENT=="
            "AGENT_FILE_VERSION_MAX",
            "fixed banks do not cover the complete version table",
        ),
        (
            "agent_file_cache_scopes[AGENT_FILE_CACHE_SCOPE_MAX]",
            "missing compact fixed-bank scope state",
        ),
        (
            "structworkflow_lifecycle_keylifecycle;",
            "bank state is not bound to a lifecycle generation",
        ),
        (
            "structworkflow_lifecycle_keyidentity_lifecycle;",
            "version entries are not bound to a lifecycle generation",
        ),
        (
            "staticuint64agent_file_system_generation;",
            "missing shared SYSTEM visibility epoch",
        ),
    ):
        require(source, fragment, message)

    lookup = function(source, "agent_file_cache_scope_locked")
    bank_lookup = function(source, "file_version_scope_state_locked")
    for fragment, message in (
        (
            "vfs_scope_lifecycle(scope_id,&lifecycle)<0",
            "dynamic cache identity bypasses the authoritative VFS lifecycle",
        ),
        (
            "returnfile_version_scope_state_locked(scope_id,lifecycle,create);",
            "authoritative lifecycle lookup bypasses the bank lookup",
        ),
    ):
        require(lookup, fragment, message)
    for fragment, message in (
        (
            "state=&agent_file_cache_scopes[AGENT_FILE_CACHE_SYSTEM_SLOT]",
            "SYSTEM lookup can enter a workflow bank",
        ),
        (
            "for(uintslot=1;slot<AGENT_FILE_CACHE_SCOPE_MAX;slot++)",
            "workflow bank lookup is not bounded by admitted workflows",
        ),
        (
            "candidate->used&&candidate->scope_id==scope_id&&"
            "workflow_lifecycle_key_equal(candidate->lifecycle,lifecycle)",
            "bank hit omits exact scope or lifecycle generation validation",
        ),
        (
            "!candidate->used&&free_state==0",
            "bank allocation can reuse an occupied lifecycle bank",
        ),
        (
            "if(!create||state->used)return0;",
            "bank creation can overwrite a live lifecycle generation",
        ),
        (
            "state->cache_generation=scope_id==VFS_SCOPE_SYSTEM?"
            "agent_file_system_generation:agent_file_generation",
            "reused banks can regress their generation baseline",
        ),
    ):
        require(bank_lookup, fragment, message)
    reject(
        bank_lookup,
        "lifecycle.id",
        "bank ownership is coupled to a recyclable lifecycle id",
    )
    require_order(
        bank_lookup,
        (
            "scope_id==VFS_SCOPE_SYSTEM",
            "state=&agent_file_cache_scopes[AGENT_FILE_CACHE_SYSTEM_SLOT]",
            "for(uintslot=1;slot<AGENT_FILE_CACHE_SCOPE_MAX;slot++)",
            "state=free_state",
            "if(!create||state->used)return0;",
            "state->lifecycle=lifecycle",
        ),
        "SYSTEM separation or lifecycle bank admission order regressed",
    )

    bank_bounds = function(source, "file_version_bank_bounds")
    for fragment, message in (
        (
            "uintbank=state-agent_file_cache_scopes",
            "version bank is not derived from its admitted scope state",
        ),
        (
            "bank==AGENT_FILE_CACHE_SYSTEM_SLOT?0:",
            "SYSTEM bank does not start at version slot zero",
        ),
        (
            "AGENT_FILE_VERSION_SYSTEM_RESIDENT+"
            "(bank-1)*AGENT_FILE_VERSION_SCOPE_RESIDENT",
            "workflow banks overlap or borrow SYSTEM capacity",
        ),
        (
            "bank==AGENT_FILE_CACHE_SYSTEM_SLOT?"
            "AGENT_FILE_VERSION_SYSTEM_RESIDENT:"
            "AGENT_FILE_VERSION_SCOPE_RESIDENT",
            "SYSTEM and workflow bank capacities are not isolated",
        ),
    ):
        require(bank_bounds, fragment, message)

    compare = function(source, "file_version_compare")
    for fragment, message in (
        ("entry->dev!=dev", "sorted inode key omits device"),
        ("entry->inum!=inum", "sorted inode key omits inode number"),
        ("entry->incarnation!=incarnation", "sorted inode key omits incarnation"),
    ):
        require(compare, fragment, message)
    require_order(
        compare,
        ("entry->dev!=dev", "entry->inum!=inum", "entry->incarnation!=incarnation"),
        "version ordering is not the full inode identity ordering",
    )

    search = function(source, "file_version_search_locked")
    for fragment, message in (
        (
            "low=0,high=state->version_count",
            "bank lookup does not search only the dense resident prefix",
        ),
        (
            "file_version_bank_bounds(state,&start,&capacity)",
            "bank lookup bypasses its fixed bounds",
        ),
        ("high>capacity", "bank lookup does not defend its resident bound"),
        ("while(low<high)", "bank lookup is not a bounded binary search"),
        (
            "file_version_compare(&agent_file_versions[start+middle],"
            "dev,inum,incarnation)",
            "binary search omits the full inode identity",
        ),
        (
            "low<state->version_count&&file_version_compare("
            "&agent_file_versions[start+low],dev,inum,incarnation)==0",
            "bank lookup does not validate the lower-bound hit",
        ),
    ):
        require(search, fragment, message)
    reject(search, "AGENT_FILE_VERSION_MAX", "bank lookup scans global capacity")

    identity = function(source, "file_version_identity_locked")
    for fragment, message in (
        (
            "file_version_identity_valid(dev,inum,incarnation,scope_id,lifecycle)",
            "version lookup accepts a partial identity",
        ),
        (
            "file_version_scope_state_locked(scope_id,lifecycle,0)",
            "version lookup can cross scope or lifecycle banks",
        ),
        (
            "file_version_search_locked(state,dev,inum,incarnation,0)",
            "version lookup omits part of the inode identity",
        ),
    ):
        require(identity, fragment, message)

    allocate = function(source, "file_version_allocate_locked")
    for fragment, message in (
        (
            "file_version_scope_state_locked(scope_id,lifecycle,1)",
            "allocation bypasses exact lifecycle bank admission",
        ),
        (
            "file_version_bank_bounds(scope_state,&start,&capacity)",
            "allocation bypasses fixed bank bounds",
        ),
        (
            "scope_state->version_count>=capacity&&"
            "file_version_evict_locked(scope_state)<0",
            "a full workflow bank can borrow another scope's capacity",
        ),
        (
            "memmove(&agent_file_versions[start+position+1],"
            "&agent_file_versions[start+position],"
            "(scope_state->version_count-position)*sizeof(*entry))",
            "allocation does not preserve the dense sorted bank",
        ),
        ("entry->scope_id=scope_id", "allocated entry omits workflow scope"),
        ("entry->dev=dev", "allocated entry omits device"),
        ("entry->inum=inum", "allocated entry omits inode number"),
        ("entry->incarnation=incarnation", "allocated entry omits incarnation"),
        (
            "entry->identity_lifecycle=lifecycle",
            "allocated entry omits lifecycle generation",
        ),
        (
            "entry->content_version="
            "agent_file_counter_next(&agent_file_content_generation);",
            "new inode binding can reuse an old content generation",
        ),
        (
            "scope_state->version_count++",
            "dense bank allocation is not accounted",
        ),
    ):
        require(allocate, fragment, message)

    clear = function(source, "file_version_clear_locked")
    for fragment, message in (
        (
            "file_version_scope_state_locked(entry->scope_id,"
            "entry->identity_lifecycle,0)",
            "entry removal can cross a lifecycle bank",
        ),
        (
            "(uint)slot<start||(uint)slot>=start+scope_state->version_count",
            "entry removal accepts a slot outside the dense bank",
        ),
        ("scope_state->version_count--", "entry removal loses bank accounting"),
        (
            "memmove(entry,entry+1,"
            "(scope_state->version_count-position)*sizeof(*entry))",
            "entry removal leaves a hole in the dense bank",
        ),
        (
            "memset(&agent_file_versions[start+scope_state->version_count],0,"
            "sizeof(*entry))",
            "entry removal retains the stale dense-bank tail",
        ),
    ):
        require(clear, fragment, message)
    reject(clear, "memset(scope_state,0", "entry eviction releases a lifecycle bank")
    reject(clear, "scope_state->used=0", "entry eviction releases a lifecycle bank")

    guard_lock = function(source, "agent_edit_lock")
    guard_unlock = function(source, "agent_edit_unlock")
    for fragment, message in (
        (
            "intenabled=intr_save()",
            "file generation guard does not preserve interrupt state",
        ),
        (
            "__sync_lock_test_and_set(&agent_file_edit_guard,1)",
            "file generation guard is not cross-CPU atomic",
        ),
        (
            "__sync_synchronize()",
            "file generation guard lacks an acquire barrier",
        ),
    ):
        require(guard_lock, fragment, message)
    require_order(
        guard_lock,
        (
            "enabled=intr_save()",
            "__sync_lock_test_and_set(&agent_file_edit_guard,1)",
            "__sync_synchronize()",
            "returnenabled",
        ),
        "file generation guard acquires in an unsafe order",
    )
    for fragment, message in (
        ("__sync_synchronize()", "file generation guard lacks a release barrier"),
        (
            "__sync_lock_release(&agent_file_edit_guard)",
            "file generation guard is not released atomically",
        ),
        ("intr_restore(enabled)", "file generation guard loses interrupt state"),
    ):
        require(guard_unlock, fragment, message)
    require_order(
        guard_unlock,
        (
            "__sync_synchronize()",
            "__sync_lock_release(&agent_file_edit_guard)",
            "intr_restore(enabled)",
        ),
        "file generation guard releases in an unsafe order",
    )

    next_generation = function(
        source, "agent_file_state_generation_next_capture_locked"
    )
    public_next = function(source, "agent_file_state_generation_next")
    for fragment, message in (
        ("intenabled=agent_edit_lock()", "generation publication is not serialized"),
        (
            "generation=agent_file_state_generation_next_capture_locked(scope_id,0)",
            "public generation publication bypasses the locked lifecycle boundary",
        ),
        ("agent_edit_unlock(enabled)", "generation publication leaves the guard held"),
    ):
        require(public_next, fragment, message)
    require_order(
        public_next,
        (
            "enabled=agent_edit_lock()",
            "agent_file_state_generation_next_capture_locked(scope_id,0)",
            "agent_edit_unlock(enabled)",
        ),
        "generation publication executes outside the cross-CPU guard",
    )
    reject(next_generation, "agent_edit_lock", "locked generation helper nests its guard")
    reject(next_generation, "agent_edit_unlock", "locked generation helper drops its caller's guard")
    reject(next_generation, "intr_save", "locked generation helper bypasses its caller's guard")
    require_order(
        next_generation,
        (
            "generation=agent_file_counter_next(&agent_file_generation)",
            "state=agent_file_cache_scope_locked(scope_id,1)",
        ),
        "global generation is not allocated before scoped publication",
    )
    for fragment, message in (
        (
            "agent_file_system_generation=generation",
            "SYSTEM updates do not publish one shared visibility epoch",
        ),
        (
            "state->cache_generation=generation",
            "workflow updates do not publish the global monotonic generation",
        ),
        (
            "if(state&&lifecycle)*lifecycle=state->lifecycle",
            "content receipts do not capture lifecycle with generation publication",
        ),
    ):
        require(next_generation, fragment, message)
    reject(
        next_generation,
        "agent_file_counter_next(&state->cache_generation)",
        "scope-local counters can regress after lifecycle bank reuse",
    )

    current_generation = function(source, "agent_file_state_scope_generation")
    for fragment, message in (
        (
            "generation=agent_file_generation",
            "unbound lifecycle lookup loses the monotonic fallback",
        ),
        (
            "generation=MAX(state->cache_generation,agent_file_system_generation)",
            "workflow generation does not include SYSTEM visibility",
        ),
    ):
        require(current_generation, fragment, message)
    for fragment, message in (
        ("intenabled=agent_edit_lock()", "generation reads are not cross-CPU serialized"),
        ("agent_edit_unlock(enabled)", "generation read leaves the guard held"),
    ):
        require(current_generation, fragment, message)
    require_order(
        current_generation,
        (
            "enabled=agent_edit_lock()",
            "state=agent_file_cache_scope_locked(scope_id,1)",
            "agent_edit_unlock(enabled)",
        ),
        "generation read executes outside the cross-CPU guard",
    )

    content_publish = function(source, "agent_file_state_content_publish")
    for fragment, message in (
        ("enabled=agent_edit_lock()", "content publication is not serialized"),
        (
            "agent_file_state_generation_next_capture_locked("
            "ip->vfs_scope_id,&lifecycle)",
            "content publication bypasses the locked generation helper",
        ),
        ("agent_edit_unlock(enabled)", "content publication leaves the guard held"),
    ):
        require(content_publish, fragment, message)
    require_order(
        content_publish,
        (
            "enabled=agent_edit_lock()",
            "agent_file_state_generation_next_capture_locked("
            "ip->vfs_scope_id,&lifecycle)",
            "agent_edit_unlock(enabled)",
        ),
        "content generation publication executes outside the cross-CPU guard",
    )
    reject(
        content_publish,
        "agent_file_state_generation_next(",
        "content publication recursively acquires the generation guard",
    )
    require_count(
        source,
        "agent_file_state_generation_next_capture_locked(",
        3,
        "locked generation helper has an unexpected or unguarded call site",
    )

    reclaim = function(source, "agent_file_state_scope_reclaim")
    for fragment, message in (
        (
            "agent_file_edits[i].scope_id==scope_id",
            "lifecycle reclaim retains edit authority",
        ),
        (
            "agent_file_digest_cache[i].scope_id==scope_id",
            "lifecycle reclaim retains digest state",
        ),
        (
            "for(uinti=0;i<AGENT_FILE_CACHE_SCOPE_MAX;i++)",
            "lifecycle reclaim does not inspect all admitted banks",
        ),
        (
            "!state->used||state->scope_id!=scope_id",
            "lifecycle reclaim can release another scope's bank",
        ),
        (
            "file_version_bank_bounds(state,&start,&capacity)",
            "lifecycle reclaim bypasses fixed bank bounds",
        ),
        (
            "memset(&agent_file_versions[start],0,"
            "capacity*sizeof(agent_file_versions[0]))",
            "lifecycle reclaim retains version entries",
        ),
        (
            "memset(state,0,sizeof(*state))",
            "lifecycle reclaim does not release its bank",
        ),
    ):
        require(reclaim, fragment, message)
    require_order(
        reclaim,
        (
            "agent_file_edits[i].scope_id==scope_id",
            "agent_file_digest_cache[i].scope_id==scope_id",
            "file_version_bank_bounds(state,&start,&capacity)",
            "memset(&agent_file_versions[start],0,"
            "capacity*sizeof(agent_file_versions[0]))",
            "memset(state,0,sizeof(*state))",
        ),
        "lifecycle bank is released before its transient state is drained",
    )
    state_output = function(source, "edit_state_locked")
    require_count(
        source.replace(state_output, ""),
        "memset(state,0,sizeof(*state))",
        2,
        "lifecycle banks are initialized or released outside admission/reclaim",
    )
    reject(source, "state->used=0", "lifecycle bank is released outside reclaim")

    unbind_state = function(source, "agent_file_state_unbind_catalog_identity")
    for fragment, message in (
        (
            "for(uinti=0;i<AGENT_FILE_CACHE_SCOPE_MAX;i++)",
            "catalog unbind does not inspect all lifecycle banks for the scope",
        ),
        (
            "!state->used||state->scope_id!=scope_id",
            "catalog unbind can invalidate another scope",
        ),
        (
            "file_version_search_locked(state,dev,inum,incarnation,0)",
            "catalog unbind omits the full inode identity",
        ),
        (
            "agent_file_counter_next(&agent_file_content_generation)",
            "catalog unbind can reuse the old content generation",
        ),
        (
            "entry->published_size_valid=0",
            "catalog unbind retains a published metadata overlay",
        ),
        (
            "file_version_digest_clear_locked(entry)",
            "catalog unbind retains digest cache state",
        ),
    ):
        require(unbind_state, fragment, message)
    reject(
        unbind_state,
        "file_version_clear_locked",
        "catalog unbind revokes the live inode version or edit lease",
    )
    reject(
        unbind_state,
        "agent_file_edits",
        "catalog unbind directly edits inode lease authority",
    )
    require(
        state_header,
        "voidagent_file_state_unbind_catalog_identity(uint64,uint64,uint64,uint);",
        "catalog unbind state transition is not shared",
    )

    reclaim_one = function(source, "agent_file_version_reclaim")
    for fragment, message in (
        (
            "for(uinti=0;i<AGENT_FILE_CACHE_SCOPE_MAX;i++)",
            "inode reclaim does not inspect every lifecycle bank for its scope",
        ),
        (
            "!state->used||state->scope_id!=ip->vfs_scope_id",
            "inode reclaim can revoke another scope",
        ),
        (
            "file_version_search_locked(state,ip->dev,ip->inum,"
            "ip->vfs_incarnation,0)",
            "inode reclaim omits the full inode identity",
        ),
        (
            "file_version_clear_locked((int)(entry-agent_file_versions))",
            "inode death retains transient state",
        ),
    ):
        require(reclaim_one, fragment, message)

    unbind = function(catalog, "staticintagent_catalog_unbind")
    require_order(
        unbind,
        (
            "agent_file_state_set_index(ip,0,0)",
            "if(result<0)returnresult;",
            "agent_file_state_unbind_catalog_identity("
            "meta->dev,meta->inum,meta->incarnation,scope_id);",
        ),
        "catalog unbind clears derived state before durable sidecar removal",
    )
    reject(
        unbind,
        "agent_file_version_reclaim",
        "catalog rollback revokes the previous inode edit lease",
    )
    require(
        unbind,
        "if(lookup_status==FS_LOOKUP_ABSENT)gotoinvalidated;",
        "missing inodes retain stale identity state",
    )

    digest_store = function(source, "agent_file_state_digest_cache_store")
    require(
        digest_store,
        "version->content_version!=expected_generation",
        "an in-flight digest can cross an identity generation change",
    )
    init = function(source, "agent_file_state_init")
    require(
        init,
        "agent_file_system_generation=0",
        "generation initialization omits the SYSTEM epoch",
    )

    # 缓存身份使用 VFS 生命周期事实；授权绝不能反向依赖缓存状态。
    reject(
        vfs,
        "agent_file_state_scope_generation",
        "VFS authorization depends on a generation cache",
    )
    reject(vfs, "agent_file_cache", "VFS authorization depends on cache internals")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"agent file generation index check failed: {error}", file=sys.stderr)
        return 1
    print("agent file generation index check passed: fixed lifecycle banks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
