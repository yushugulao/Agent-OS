#!/usr/bin/env python3
"""Validate the explicit, volatile Agent Live-Query FS architecture."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(ValueError):
    pass


SOURCE_PATHS = {
    "catalog": "os/agent_metadata_catalog.c",
    "catalog_h": "os/agent_metadata_catalog.h",
    "query": "os/agent_metadata_query.c",
    "directory": "os/agent_metadata_directory.c",
    "objects": "os/agent_metadata_objects.c",
    "core": "os/agent_core.c",
    "events": "os/agent_live_query_events.c",
    "events_h": "os/agent_live_query_events.h",
    "ipc": "os/agent_ipc.c",
    "agent_h": "os/agent.h",
    "user_h": "user/include/agent.h",
    "user_syscall": "user/lib/syscall.c",
    "file_state": "os/agent_file_state.c",
}


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def function(source: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", source)
    if match is None:
        raise ContractError(f"missing function {name}")
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ContractError(f"unterminated function {name}")


def require(source: str, token: str, message: str) -> None:
    if token not in source:
        raise ContractError(message)


def reject(source: str, token: str, message: str) -> None:
    if token in source:
        raise ContractError(message)


def require_order(source: str, tokens: tuple[str, ...], message: str) -> None:
    cursor = -1
    for token in tokens:
        cursor = source.find(token, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def load_sources(root: Path) -> dict[str, str]:
    return {
        name: compact((root / relative).read_text(encoding="utf-8"))
        for name, relative in SOURCE_PATHS.items()
    }


def validate_sources(sources: dict[str, str]) -> None:
    catalog = sources["catalog"]
    catalog_h = sources["catalog_h"]
    query = sources["query"]
    directory = sources["directory"]
    objects = sources["objects"]
    core = sources["core"]
    events = sources["events"]
    events_h = sources["events_h"]
    ipc = sources["ipc"]
    agent_h = sources["agent_h"]
    user_h = sources["user_h"]
    user_syscall = sources["user_syscall"]
    file_state = sources["file_state"]
    # Only explicitly registered records enter the resident index; retired
    # persistence/autoscan bits are rejected before the Catalog is reached.
    require(catalog, "agent_catalog_live_query_bits", "missing resident live-query bitmap")
    derived_add = function(catalog, "agent_catalog_derived_add")
    derived_remove = function(catalog, "agent_catalog_derived_remove")
    require(
        derived_add,
        "agent_catalog_bitmap_set(agent_catalog_live_query_bits,slot)",
        "explicit rows are not published to the live-query index",
    )
    require(
        derived_remove,
        "agent_catalog_bitmap_clear(agent_catalog_live_query_bits,slot)",
        "removed rows leave stale index membership",
    )
    # Snapshot and selector identity include the full workflow generation and inode incarnation.
    read_begin = function(catalog, "agent_metadata_catalog_read_begin")
    read_copy = function(catalog, "agent_metadata_catalog_read_copy")
    read_end = function(catalog, "agent_metadata_catalog_read_end")
    resolve = function(catalog, "agent_metadata_catalog_resolve")
    key_matches = function(catalog, "agent_catalog_key_matches")
    require(read_begin, "agent_catalog_live_query_bits", "snapshot query bypasses explicit membership")
    require(read_begin, "agent_catalog_mutation_owner!=0", "query can start during a mutation")
    require(read_copy, "workflow_lifecycle_key_equal(lifecycle,snapshot->lifecycle)", "record copy crosses workflow generations")
    require(read_end, "workflow_lifecycle_key_equal(lifecycle,snapshot->lifecycle)", "snapshot completion crosses workflow generations")
    require(read_end, "snapshot->fs_generation==agent_file_state_scope_generation", "content generation is not revalidated")
    require(resolve, "agent_catalog_slot_lifecycle(slot),lifecycle", "selector resolution ignores workflow generation")
    for token in (
        "selector->dev==meta->dev",
        "selector->inum==meta->inum",
        "selector->incarnation==meta->incarnation",
    ):
        require(key_matches, token, f"selector identity omits {token}")

    # The public mutation ABI is now volatile-only and never enters store/scan paths.
    meta_set = function(objects, "agent_file_meta_set_execute")
    require(
        meta_set,
        "meta.flags&(AGENT_FILE_META_F_PERSIST|AGENT_FILE_META_F_AUTOSCAN)",
        "legacy persistence flags are not rejected",
    )
    require(meta_set, "agent_metadata_catalog_edit_commit_volatile", "set bypasses volatile commit")
    require(meta_set, "agent_metadata_catalog_bind_volatile", "set bypasses volatile bind")
    require(meta_set, "agent_metadata_catalog_clear_slot_volatile", "delete bypasses volatile clear")
    admission = function(objects, "agent_metadata_admission_status")
    require(admission, "returnAGENT_STATUS_OK", "Agent admission still depends on catalog recovery")

    # Batch registration amortizes ingress coordination while preserving ordered
    # per-item commits, statuses, and fatal-prefix semantics.
    for header in (agent_h, user_h):
        require(header, "AGENT_FILE_META_BATCH_MAX16", "metadata batch capacity is absent from UAPI")
        require(header, "AGENT_FILE_META_BATCH_F_NONE0", "metadata batch flags are absent from UAPI")
        require(header, "agent_file_meta_set_batch", "metadata batch API is absent from UAPI")
    require(
        user_syscall,
        "syscall(SYS_agent_file_meta_set_batch,items,statuses,count,flags)",
        "metadata batch wrapper does not forward all four arguments",
    )
    scalar_set = function(objects, "sys_agent_file_meta_set")
    batch_set = function(objects, "sys_agent_file_meta_set_batch")
    batch_execute = function(objects, "agent_file_meta_set_batch_execute")
    require(
        objects,
        "staticstructagent_file_metaagent_file_meta_batch_scratch[AGENT_FILE_META_BATCH_MAX]",
        "metadata batch stages records on the kernel stack",
    )
    require(
        scalar_set,
        "agent_file_meta_set_execute(p,metaaddr,0,scope_id,0,0)",
        "scalar metadata set no longer shares the per-item mutation core",
    )
    require(
        batch_execute,
        "agent_file_meta_set_execute(p,0,&agent_file_meta_batch_scratch[i],scope_id,1,&stop_batch)",
        "metadata batch bypasses the scalar per-item mutation core",
    )
    require_order(
        batch_set,
        (
            "flags!=0||count<0||count>AGENT_FILE_META_BATCH_MAX",
            "if(count==0)return0",
            "itemsaddr>=MAXVA||statusesaddr>=MAXVA",
            "sizeof(structagent_file_meta)>MAXVA-itemsaddr",
            "sizeof(int)>MAXVA-statusesaddr",
            "workflow_lifecycle_operation_enter(lifecycle)",
            "agent_file_meta_set_batch_execute",
            "workflow_lifecycle_operation_leave(lifecycle)",
        ),
        "metadata batch validation and lifecycle gate are not ordered",
    )
    if batch_set.count("workflow_lifecycle_operation_enter(lifecycle)") != 1 or batch_set.count(
        "workflow_lifecycle_operation_leave(lifecycle)"
    ) != 1:
        raise ContractError("metadata batch enters its lifecycle more than once")
    require_order(
        batch_execute,
        (
            "agent_file_meta_write_scope(p,&scope_id)",
            "itemsaddr<statuses_end&&statusesaddr<items_end",
            "agent_metadata_txn_lock(1)",
            "proc_vm_snapshot_begin(p)",
            "user_range_check(p->pagetable,itemsaddr",
            "user_range_check(p->pagetable,statusesaddr",
            "copyin(p->pagetable,(char*)&agent_file_meta_batch_scratch[i],itemaddr",
            "copyin(p->pagetable,(char*)&preserved,statusaddr",
            "copyout(p->pagetable,statusaddr,(char*)&preserved",
            "proc_vm_snapshot_end(p)",
            "agent_file_meta_set_execute",
            "if(item_status==-1)break",
            "copyout(p->pagetable,statusesaddr",
            "processed++",
            "if(stop_batch||item_status==AGENT_STATUS_INDETERMINATE)break",
            "agent_metadata_txn_work_charge(1)",
            "agent_metadata_txn_unlock()",
        ),
        "metadata batch no longer publishes one ordered determined prefix",
    )
    require(
        batch_execute,
        "if(copyout(p->pagetable,statusesaddr+(uint64)i*sizeof(item_status),(char*)&item_status,sizeof(item_status))<0){processed=AGENT_STATUS_INDETERMINATE;break;}",
        "an impossible post-preflight status failure can hide a committed item",
    )
    if batch_execute.count("agent_metadata_txn_lock(1)") != 1:
        raise ContractError("metadata batch does not share one metadata transaction")
    require(
        batch_execute,
        "agent_metadata_txn_unlock();returnprocessed;",
        "metadata batch success leaks the metadata transaction",
    )
    require_order(
        batch_execute,
        (
            "out_snapshot_error:",
            "proc_vm_snapshot_end(p)",
            "out_txn_error:",
            "agent_metadata_txn_unlock()",
        ),
        "metadata batch preflight errors leak the metadata transaction",
    )

    # One in-memory transaction drains deltas and captures the same lifecycle cut.
    current_fence = function(objects, "agent_metadata_quiescence_fence_snapshot_current")
    require_order(
        current_fence,
        (
            "agent_metadata_txn_lock(1)",
            "agent_live_query_fence_drain(lifecycle,scope_id)",
            "agent_metadata_catalog_fence_generation(scope_id,lifecycle,metadata_generation)",
            "agent_metadata_txn_unlock()",
        ),
        "workflow metadata fence is not one resident-index transaction",
    )
    catalog_fence = function(catalog, "agent_metadata_catalog_fence_generation")
    require(catalog_fence, "agent_catalog_require_txn()", "catalog fence is not transaction-owned")
    require(catalog_fence, "workflow_lifecycle_key_equal", "catalog fence ignores lifecycle generation")
    require(catalog_fence, "agent_catalog_mutation_owner!=0", "catalog fence can split a mutation")
    require(catalog_fence, "agent_catalog_active_edit!=0", "catalog fence can expose an active edit")
    require(catalog_fence, "agent_file_state_scope_generation(scope_id)", "catalog fence omits workflow file generation")
    require(catalog_fence, "agent_file_state_scope_generation(VFS_SCOPE_SYSTEM)", "catalog fence omits SYSTEM visibility")
    require(catalog_h, "agent_metadata_catalog_fence_generation", "catalog fence API is not declared")

    # Directory hooks never discover ordinary files and never lose a contended exact delete.
    publish = function(directory, "agent_fs_publish_content")
    remove = function(directory, "agent_fs_remove_inode")
    for name in ("agent_fs_note_write", "agent_fs_note_truncate"):
        require(function(directory, name), "if(FS_META_UNBOUND(ip))return", f"ordinary {name} enters metadata")
    require(publish, "agent_live_query_content_enqueue", "bound writes do not queue an UPDATE")
    require(remove, "if(FS_META_UNBOUND(ip))", "ordinary unlink enters metadata")
    require(remove, "agent_metadata_txn_try_external()", "unlink can block on metadata coordination")
    reject(remove, "agent_metadata_txn_lock", "unlink takes the blocking metadata gate")
    if remove.count("agent_live_query_tombstone_enqueue") != 2:
        raise ContractError("unlink does not queue exact tombstones for both contention and retry")
    require(remove, "agent_metadata_catalog_remove_identity_exact", "unlink does not verify full inode identity")
    exact_remove = function(catalog, "agent_metadata_catalog_remove_identity_exact")
    for token in ("lifecycle", "scope_id", "dev", "inum", "incarnation"):
        require(exact_remove, token, f"deferred delete omits {token}")
    for token in (
        "agent_catalog_files[slot].dev!=dev",
        "agent_catalog_files[slot].inum!=inum",
        "agent_catalog_files[slot].incarnation!=incarnation",
        "!workflow_lifecycle_key_equal(agent_catalog_slot_lifecycle(slot),lifecycle)",
    ):
        require(exact_remove, token, f"deferred delete fails to verify {token}")

    # Drains snapshot under IRQ, but catalog mutation and event publication happen with IRQs restored.
    tombstone_process = function(events, "agent_live_query_tombstone_process")
    content_process = function(events, "agent_live_query_content_process")
    require_order(
        tombstone_process,
        ("snapshot=agent_live_query_tombstones[slot]", "intr_restore(enabled)", "agent_metadata_catalog_remove_identity_exact"),
        "tombstone catalog removal runs in an IRQ-off region",
    )
    require_order(
        content_process,
        ("snapshot=agent_live_query_content_pending[slot]", "intr_restore(enabled)", "agent_metadata_catalog_borrow"),
        "content projection runs in an IRQ-off region",
    )
    for name in ("agent_live_query_tombstone_drain", "agent_live_query_content_drain"):
        body = function(events, name)
        reject(body, "enabled=intr_save();while", f"{name} holds IRQs across the full drain")

    # Typed watches compile the full predicate and emit true ENTER/UPDATE/LEAVE deltas.
    for header in (agent_h, user_h):
        require(header, "AGENT_EVENT_FILE_QUERY", "typed live-query event is absent from UAPI")
        require(header, "structagent_file_live_watch", "typed watch request is absent from UAPI")
        require(header, "AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED", "typed watch has no sticky resync state")
    require(user_syscall, "agent_live_watch", "typed watch user wrapper is missing")
    require(user_syscall, "agent_live_unwatch", "typed unwatch user wrapper is missing")
    execute_op = function(core, "agent_execute_op")
    require(
        execute_op,
        "if(op->arg0>AGENT_EVENT_MAX||op->arg0==AGENT_EVENT_FILE_QUERY)",
        "generic Agent tool watches do not reject FILE_QUERY",
    )
    require_order(
        execute_op,
        (
            "caseAGENT_TOOL_AGENT_WATCH:",
            "op->arg0==AGENT_EVENT_FILE_QUERY",
            "agent_ipc_watch_set(p,op->arg0,op->payload)",
        ),
        "generic Agent tool watches can allocate an unremovable FILE_QUERY slot",
    )
    install = function(events, "agent_live_query_watch_install_typed")
    require(install, "agent_metadata_txn_owned(0)", "watch install is not serialized with mutation")
    require(install, "agent_live_query_predicate_compile", "watch install stores no complete predicate")
    require(install, "agent_metadata_catalog_fence_generation", "watch install has no snapshot generation handshake")
    require(install, "subscription->key=key", "watch subscription omits workflow generation")
    require(install, "subscription->resync_generation=resync_generation", "watch install loses sticky resync")
    typed_delta = function(events, "agent_live_query_typed_target_changes")
    for token in (
        "!before_matches&&after_matches",
        "before_matches&&after_matches",
        "elseif(before_matches)",
        "AGENT_LIVE_QUERY_ENTER",
        "AGENT_LIVE_QUERY_UPDATE",
        "AGENT_LIVE_QUERY_LEAVE",
    ):
        require(typed_delta, token, f"typed delta truth table omits {token}")
    publish_transition = function(events, "agent_live_query_publish_transition")
    require(publish_transition, "AGENT_EVENT_FILE_QUERY", "typed deltas are not delivered")
    require(publish_transition, "agent_live_query_proc_resync_mark_locked", "queue overflow is not sticky")
    require_order(
        publish_transition,
        (
            "agent_live_query_domain_valid",
            "agent_live_query_file_watches_present()",
            "for(structproc*target=pool",
        ),
        "zero-watch fast path does not preserve validation before skipping the process scan",
    )
    require_order(
        publish_transition,
        ("typed_changes=agent_live_query_typed_target_changes", "intr_restore(enabled)", "agent_ipc_deliver_live_event"),
        "event delivery holds the live-index IRQ snapshot",
    )
    if publish_transition.count("intr_restore(enabled)") != 4:
        raise ContractError("live-query transition has an unreviewed IRQ boundary")

    # The fast-path count must be exact: installs increment once, removals and
    # process reuse can return it to zero, and every read is IRQ protected.
    watch_refresh = function(events, "agent_live_query_file_watch_refresh_locked")
    watch_clear = function(events, "agent_live_query_file_watch_clear_locked")
    watches_present = function(events, "agent_live_query_file_watches_present")
    live_proc_reset = function(events, "agent_live_query_proc_reset")
    events_init = function(events, "agent_live_query_events_init")
    has_file_watch = function(events, "agent_live_query_has_file_watch")
    require_order(
        events_init,
        (
            "memset(agent_live_query_file_watch_present,0",
            "agent_live_query_file_watch_processes=0",
        ),
        "file-watch fast-path state is not initialized empty",
    )
    require(has_file_watch, "AGENT_EVENT_FILE_STATUS", "legacy file-status watches are absent from the fast-path count")
    require(has_file_watch, "AGENT_EVENT_FILE_QUERY", "typed file-query watches are absent from the fast-path count")
    for token in (
        "agent_live_query_file_watch_present[slot]=1",
        "agent_live_query_file_watch_processes++",
        "agent_live_query_file_watch_present[slot]=0",
        "agent_live_query_file_watch_processes--",
    ):
        require(watch_refresh, token, f"file-watch count refresh omits {token}")
    require(watch_clear, "agent_live_query_file_watch_processes--", "process reset cannot clear the file-watch count")
    require_order(
        watches_present,
        ("intr_save()", "agent_live_query_file_watch_processes!=0", "intr_restore(enabled)"),
        "zero-watch count is read without IRQ synchronization",
    )
    typed_remove = function(events, "agent_live_query_watch_remove_typed")
    require(install, "agent_live_query_file_watch_refresh_locked(p)", "typed watch install does not publish its fast-path count")
    require(typed_remove, "agent_live_query_file_watch_refresh_locked(p)", "typed unwatch does not clear its fast-path count")
    require(live_proc_reset, "agent_live_query_file_watch_clear_locked(p)", "process reuse leaks the fast-path watch count")
    generic_watch = function(ipc, "agent_ipc_watch_set")
    generic_unwatch = function(ipc, "agent_ipc_watch_clear")
    require(generic_watch, "agent_live_query_file_watch_changed(p)", "legacy file watch install bypasses the fast-path count")
    require(generic_unwatch, "agent_live_query_file_watch_changed(p)", "legacy file unwatch bypasses the fast-path count")
    require_order(
        generic_unwatch,
        (
            "if(!cold->watch_valid[i])continue",
            "if(cold->watch_event_type[i]==AGENT_EVENT_FILE_QUERY)continue",
            "if(!clear_all)",
            "cold->watch_valid[i]=0",
        ),
        "generic clear-all can detach a typed watch without its watch-id protocol",
    )
    fence_drain = function(events, "agent_live_query_fence_drain")
    require(fence_drain, "agent_live_query_domain_generation_locked", "fence ignores overflow resync state")
    require(fence_drain, "agent_live_query_proc_resync_pending_domain", "fence ignores undelivered resync markers")
    require(events_h, "agent_live_query_fence_drain", "live-query fence drain is not declared")

    # IPC keeps typed delivery out of substring matching and isolates lifecycle generations.
    same_scope = function(ipc, "agent_ipc_same_scope")
    require(same_scope, "workflow_lifecycle_key_equal(left_key,right_key)", "IPC compares only numeric scope")
    deliver = function(ipc, "agent_ipc_deliver_live_event")
    require(deliver, "AGENT_EVENT_FILE_QUERY", "IPC rejects typed live-query events")
    queue = function(ipc, "agent_ipc_queue_event_locked")
    require(queue, "delivery==AGENT_EVENT_LIVE_QUERY_TARGETED", "typed delivery has no explicit mode")
    require(queue, "delivery==AGENT_EVENT_REQUIRE_WATCH&&!agent_ipc_filter_matches", "legacy substring filter is not isolated")
    watch_update = function(ipc, "agent_ipc_watch_update")
    require(watch_update, "event_type==AGENT_EVENT_FILE_QUERY", "typed watch can fall through to string filters")
    proc_reset = function(ipc, "agent_ipc_proc_reset")
    require(proc_reset, "agent_live_query_proc_reset(p)", "process reuse leaks typed subscriptions")

    # File size projection remains generation-bound even though metadata is volatile.
    project = function(file_state, "agent_file_state_project_hit")
    require_order(
        project,
        ("agent_file_state_overlay_published_size(&snapshot,scope_id)", "hit->size=snapshot.size", "hit->fs_generation=snapshot.fs_generation"),
        "query hit size is not bound to its content generation",
    )


def check(root: Path) -> None:
    validate_sources(load_sources(root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root)
    except (ContractError, OSError) as exc:
        print(f"Agent Live-Query FS check failed: {exc}", file=sys.stderr)
        return 1
    print("Agent Live-Query FS check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
