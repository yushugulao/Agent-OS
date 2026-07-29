#!/usr/bin/env python3
"""Static contract for catalog rollback isolation across metadata I/O."""

from __future__ import annotations

import argparse
from pathlib import Path


class ContractError(RuntimeError):
    pass


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        start = source.find(signature, start + len(signature))
    if start < 0:
        raise ContractError(f"missing function: {signature}")
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start : pos + 1]
    raise ContractError(f"unterminated body: {signature}")


def require(source: str, token: str, label: str) -> None:
    if token not in source:
        raise ContractError(f"{label}: missing {token}")


def require_order(source: str, tokens: tuple[str, ...], label: str) -> None:
    positions = [source.find(token) for token in tokens]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        raise ContractError(f"{label}: wrong order: {tokens}")


def validate_sources(sources: dict[str, str]) -> None:
    agent_h = sources["agent_h"]
    catalog = sources["catalog"]
    catalog_h = sources["catalog_h"]
    metadata = sources["metadata"]
    objects = sources["objects"]
    actions = sources["actions"]
    fs = sources["fs"]
    fs_h = sources["fs_h"]
    file_source = sources["file"]
    store_io = sources["store_io"]
    store = sources["store"]
    vfs = sources["vfs"]

    for token in (
        "#define AGENT_FILE_META_MAX       512",
        "#define AGENT_FILE_SYSTEM_LIMIT   64",
        "#define AGENT_FILE_SCOPE_LIMIT    112",
        "(AGENT_FILE_META_MAX - AGENT_FILE_SYSTEM_LIMIT)",
    ):
        require(agent_h, token, "fixed catalog partition")
    for token in (
        "AGENT_FILE_SCOPE_GUARANTEE",
        "AGENT_FILE_SCOPE_BURST_LIMIT",
    ):
        if token in agent_h + catalog + fs + vfs:
            raise ContractError(
                f"fixed catalog partition regained an elastic limit: {token}"
            )

    for token in (
        "AGENT_FILE_SYSTEM_LIMIT +",
        "AGENT_FILE_ORDINARY_LIMIT == AGENT_FILE_META_MAX",
        "VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT ==",
        "AGENT_FILE_ORDINARY_LIMIT",
    ):
        require(catalog, token, "fixed catalog partition proof")
    scope_admissible = function_body(catalog, "agent_catalog_scope_admissible(")
    for token in (
        "vfs_scope_lifecycle(scope_id, lifecycle)",
        "workflow_lifecycle_active(*lifecycle)",
        "workflow_lifecycle_closing(*lifecycle)",
    ):
        require(scope_admissible, token, "catalog lifecycle admission")
    admission = function_body(catalog, "agent_catalog_admission(")
    for token in (
        "AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT",
        "result.owned >= limit",
        "result.ordinary >= AGENT_FILE_ORDINARY_LIMIT",
        "return AGENT_CATALOG_NO_SPACE",
        "!growth || scope_id == VFS_SCOPE_SYSTEM",
        "!agent_catalog_scope_admissible(scope_id, &lifecycle)",
        "return AGENT_CATALOG_INTERRUPTED",
    ):
        require(admission, token, "fixed per-scope catalog admission")
    plan_count = function_body(catalog, "agent_catalog_plan_count(")
    for token in (
        "AGENT_FILE_SYSTEM_LIMIT",
        "AGENT_FILE_ORDINARY_LIMIT",
        "AGENT_FILE_SCOPE_LIMIT",
    ):
        require(plan_count, token, "snapshot fixed partition admission")
    scope_create = function_body(vfs, "static int vfs_scope_create(")
    for token in (
        "if (ref->retiring)",
        "retiring++",
        "workflow_lifecycle_active(ref->lifecycle)",
        "workflow_lifecycle_closing(ref->lifecycle)",
        "allocated + retiring < VFS_SCOPE_MAX_ACTIVE",
    ):
        require(scope_create, token, "retiring catalog partition retention")

    for token in (
        "uint64 candidate_epoch, catalog_generation, lifecycle_generation;",
        "uint lifecycle_id;",
        "plan_lifecycle_generation",
        "plan_lifecycle_id",
    ):
        require(catalog_h, token, "immutable scoped reload lifecycle key")
    plan_key = function_body(catalog, "static struct agent_catalog_plan_key agent_catalog_plan_key(")
    for token in (
        "memset(&key, 0, sizeof(key))",
        "key.lifecycle_id = lifecycle.id",
        "key.lifecycle_generation = lifecycle.generation",
    ):
        require(plan_key, token, "scoped reload key construction")
    plan_matches = function_body(catalog, "agent_catalog_plan_matches(")
    for token in (
        "left->lifecycle_id == right->lifecycle_id",
        "left->lifecycle_generation == right->lifecycle_generation",
    ):
        require(plan_matches, token, "scoped reload key comparison")
    prepare = function_body(catalog, "agent_metadata_catalog_prepare_snapshot(")
    prepare_key = prepare.find("key = agent_catalog_plan_key(")
    prepare_match = prepare.find("if (!agent_catalog_plan_matches(")
    if prepare_key < 0 or prepare_match < prepare_key:
        raise ContractError("scoped reload prepare key order is incomplete")
    initial_revalidation = prepare[:prepare_key]
    continuation_revalidation = prepare[prepare_key:prepare_match]
    for token in (
        "reload_one_scope &&",
        "!agent_catalog_scope_admissible(reload_scope, &lifecycle)",
        "AGENT_METADATA_LOAD_INTERRUPTED",
    ):
        require(initial_revalidation, token, "scoped reload prepare admission")
    for token in (
        "result->plan_lifecycle_id != lifecycle.id",
        "result->plan_lifecycle_generation != lifecycle.generation",
        "agent_catalog_prepare_fail(",
        "AGENT_METADATA_LOAD_INTERRUPTED",
    ):
        require(continuation_revalidation, token,
                "scoped reload prepare continuation")
    apply = function_body(catalog, "agent_metadata_catalog_apply_snapshot(")
    apply_key = apply.find("key = agent_catalog_plan_key(")
    if apply_key < 0:
        raise ContractError("scoped reload apply key is missing")
    apply_revalidation = apply[:apply_key]
    for token in (
        "reload_one_scope &&",
        "!agent_catalog_scope_admissible(reload_scope, &lifecycle)",
        "result->plan_lifecycle_id != lifecycle.id",
        "result->plan_lifecycle_generation != lifecycle.generation",
        "agent_catalog_prepare_fail(",
        "AGENT_METADATA_LOAD_INTERRUPTED",
    ):
        require(apply_revalidation, token, "scoped reload apply revalidation")

    for token in (
        "struct agent_catalog_mutation_fence",
        "struct agent_catalog_undo_token",
        "fence_token, catalog_generation, slot_binding",
        "agent_metadata_catalog_mutation_begin(",
        "agent_metadata_catalog_mutation_end(",
        "agent_metadata_catalog_undo_capture(",
        "agent_metadata_catalog_undo_note_created(",
        "AGENT_CATALOG_UNDO_CREATED",
        "AGENT_CATALOG_INDETERMINATE",
    ):
        require(catalog_h, token, "opaque rollback protocol")

    allowed = function_body(
        catalog, "agent_catalog_mutation_allowed("
    )
    for token in ("agent_catalog_mutation_owner == 0",
                  "agent_metadata_txn_token()"):
        require(allowed, token, "owner mutation fence")
    begin = function_body(catalog, "agent_metadata_catalog_mutation_begin(")
    for token in ("agent_catalog_require_txn()",
                  "agent_catalog_mutation_owner != 0",
                  "agent_catalog_active_edit != 0",
                  "agent_metadata_txn_token()",
                  "fence->token = agent_catalog_mutation_token"):
        require(begin, token, "fence acquisition")
    end = function_body(catalog, "agent_metadata_catalog_mutation_end(")
    for token in ("agent_catalog_fence_owned(fence)",
                  "clean = agent_catalog_active_edit == 0",
                  "agent_catalog_active_edit = 0",
                  "agent_catalog_mutation_owner = 0",
                  "agent_catalog_mutation_token = 0",
                  "fence->token = 0"):
        require(end, token, "fence release")

    binding = function_body(catalog, "agent_catalog_undo_binding(")
    for token in ("&undo->fence_token", "&undo->catalog_generation",
                  "&undo->reserved", "&slot",
                  "&agent_catalog_scopes[slot]",
                  "&agent_catalog_states[slot]",
                  "&agent_catalog_files[slot]"):
        require(binding, token, "full post-state binding")
    capture = function_body(catalog, "agent_metadata_catalog_undo_capture(")
    for token in ("agent_catalog_fence_owned(fence)",
                  "undo->fence_token = fence->token",
                  "undo->catalog_generation = agent_catalog_generation",
                  "undo->slot_binding = agent_catalog_undo_binding("):
        require(capture, token, "catalog-issued undo token")
    created = function_body(
        catalog, "agent_metadata_catalog_undo_note_created("
    )
    for token in ("agent_catalog_fence_owned(fence)",
                  "undo->reserved != 0",
                  "agent_metadata_catalog_identity_state(&agent_catalog_files[slot]) <= 0",
                  "undo->slot_binding != agent_catalog_undo_binding(undo, slot)",
                  "undo->reserved = AGENT_CATALOG_UNDO_CREATED",
                  "undo->slot_binding = agent_catalog_undo_binding(undo, slot)"):
        require(created, token, "catalog-issued creation receipt")
    require_order(
        created,
        ("undo->slot_binding != agent_catalog_undo_binding(undo, slot)",
         "undo->reserved = AGENT_CATALOG_UNDO_CREATED",
         "undo->slot_binding = agent_catalog_undo_binding(undo, slot)"),
        "validate then bind creation receipt",
    )

    restore = function_body(catalog, "int agent_metadata_catalog_restore(")
    for token in ("agent_catalog_fence_owned(fence)",
                  "(undo->reserved & ~AGENT_CATALOG_UNDO_CREATED) != 0",
                  "undo->fence_token != fence->token",
                  "undo->slot_binding != agent_catalog_undo_binding(undo, slot)",
                  "agent_catalog_admission(",
                  "previous_scope, slot, previous, 0",
                  "agent_catalog_unbind(slot",
                  "undo->reserved & AGENT_CATALOG_UNDO_CREATED",
                  "fs_rollback_created_workflow("):
        require(restore, token, "verified rollback")
    require_order(
        restore,
        ("undo->slot_binding != agent_catalog_undo_binding(undo, slot)",
         "agent_catalog_admission(",
         "agent_catalog_unbind(slot",
         "fs_rollback_created_workflow(",
         "agent_catalog_state_clear(slot)"),
        "validate and admit before rollback write",
    )
    if "previous_scope, slot, previous, 1" in restore:
        raise ContractError("exact rollback is incorrectly classified as growth")
    if "undo->catalog_generation != agent_catalog_generation" in restore:
        raise ContractError("unrelated catalog generation became a rollback veto")
    restore_cleanup = restore[
        restore.find("if ((undo->reserved & AGENT_CATALOG_UNDO_CREATED)"):
        restore.find("agent_catalog_state_clear(slot)")
    ]
    for token in ("fs_rollback_created_workflow(", ") < 0)", "return -1;"):
        require(restore_cleanup, token, "created receipt cleanup failure")

    changed = function_body(catalog, "static void agent_catalog_changed(")
    require(changed, "!agent_catalog_mutation_allowed()",
            "last-line mutation invariant")
    guarded_mutators = (
        "static int agent_catalog_edit_begin(",
        "int agent_metadata_catalog_edit_commit(",
        "static int agent_catalog_unbind(int slot",
        "static int agent_catalog_bind_status(",
        "int agent_metadata_catalog_bind(",
        "int agent_metadata_catalog_clear_slot(",
        "agent_metadata_catalog_reconcile_slot(int slot)",
        "int agent_metadata_catalog_reclaim_scope(",
        "agent_metadata_catalog_apply_snapshot(",
    )
    for signature in guarded_mutators:
        body = function_body(catalog, signature)
        require(body, "if (!agent_catalog_mutation_allowed())",
                f"mutation guard {signature}")
    require(apply, "return AGENT_METADATA_LOAD_INTERRUPTED;",
            "reload fence retry")

    lookup = function_body(
        catalog, "agent_catalog_lookup_or_create_status("
    )
    for token in ("int *created", "*created = 0",
                  "fs_create(name, T_FILE, created",
                  "else if (ip == 0 && status)\n\t\t*status = lookup_status"):
        require(lookup, token, "create provenance")
    bind_status = function_body(catalog, "static int agent_catalog_bind_status(")
    for token in ("lookup_status, &create",
                  "create == FS_CREATE_INDETERMINATE ?",
                  "return create;", "if (create) {",
                  "return fs_rollback_created_workflow(meta->physical_name",
                  "AGENT_CATALOG_INDETERMINATE : -1"):
        require(bind_status, token, "bind-local create rollback")
    mismatch = bind_status[
        bind_status.find("if ((meta->dev"):
        bind_status.find("old_slot = ip->agent_meta_slot")
    ]
    if "iput(ip)" in mismatch:
        raise ContractError("bind-local cleanup drops the creation identity early")
    cleanup = bind_status[bind_status.find("\nout:"):]
    require_order(cleanup, ("uint dev = ip->dev, inum = ip->inum",
                            "uint incarnation = ip->vfs_incarnation",
                            "iput(ip)",
                            "return fs_rollback_created_workflow("),
                  "release captured bind reference before exact cleanup")
    after_release = cleanup[cleanup.find("iput(ip)") + len("iput(ip)"):]
    if "ip->" in after_release:
        raise ContractError("bind-local cleanup uses inode after reference drop")
    bind = function_body(catalog, "int agent_metadata_catalog_bind(")
    require(bind, "if (result >= 0)", "created bind success generation")

    require(fs_h, "fs_rollback_created_workflow(",
            "filesystem creation rollback API")
    for token in ("FS_LOOKUP_INDETERMINATE", "FS_CREATE_INDETERMINATE"):
        require(fs_h, token, "namespace publication state")
    dirlink = function_body(fs, "int dirlink(")
    for token in ("fs_io_health == FS_IO_INDETERMINATE ?\n"
                  "\t\t\tFS_LOOKUP_INDETERMINATE",
                  "result = fs_durable_barrier_forward()",
                  "return FS_LOOKUP_INDETERMINATE;"):
        require(dirlink, token, "directory publication state")
    classifier = function_body(fs, "static int fs_create_failure_status(")
    for token in ("fs_io_health == FS_IO_INDETERMINATE",
                  "FS_LOOKUP_INDETERMINATE"):
        require(classifier, token, "unified create failure classifier")
    create_inode = function_body(fs, "struct inode *fs_create(")
    if create_inode.count("fs_create_failure_status(") < 6:
        raise ContractError("create failures bypass the health classifier")
    for token in ("result = dirlink(dp, path, ip->inum, cred);\n"
                  "\tif (result < 0)\n\t\tgoto fail_allocated;",
                  "fail_allocated:",
                  "result = fs_create_failure_status(result)",
                  "if (result == FS_LOOKUP_INDETERMINATE)\n\t\tiput(ip)",
                  "ip->removed = 1",
                  "if (fs_put_removed_checked(ip) < 0)",
                  "*created = FS_CREATE_INDETERMINATE",
                  "*status = result"):
        require(create_inode, token, "create publication failure")
    if "iabort(ip)" in create_inode:
        raise ContractError("create failure uses unchecked inode abort")
    require_order(
        create_inode,
        ("result = iupdate(ip)", "result = fs_durable_barrier_forward()",
         "result = dirlink(dp, path, ip->inum, cred)", "fail_allocated:"),
        "classify before checked create cleanup",
    )
    allocated_cleanup = create_inode[create_inode.find("fail_allocated:"):]
    require_order(
        allocated_cleanup,
        ("result = fs_create_failure_status(result)",
         "if (result == FS_LOOKUP_INDETERMINATE)", "ip->removed = 1",
         "fs_put_removed_checked(ip)"),
        "classify before checked create cleanup",
    )
    require(
        allocated_cleanup,
        "if (created && result == FS_LOOKUP_INDETERMINATE)\n"
        "\t\t*created = FS_CREATE_INDETERMINATE;",
        "allocated inode publication status",
    )

    fileopen = function_body(file_source, "int fileopen(")
    create_open = fileopen[fileopen.find("if (omode & O_CREATE) {"):
                           fileopen.find("} else {")]
    require(create_open, "if (ip == 0)\n\t\t\tgoto fail;",
            "file create rejects indeterminate publication")
    bank_lookup = function_body(store_io, "agent_meta_store_io_lookup_bank(")
    bank_create = bank_lookup[bank_lookup.find("ip = fs_create("):]
    require_order(bank_create, ("if (ip == 0)", "*status_out = status",
                                "return 0;"),
                  "metadata store preserves create publication status")
    device_error = function_body(store, "agent_meta_persist_device_error(")
    for token in ("result == FS_LOOKUP_INDETERMINATE",
                  "agent_metadata_store_fail_closed_runtime()",
                  "AGENT_METADATA_PERSIST_FAIL_CLOSED",
                  "agent_meta_persist.irrevocable = 1"):
        require(device_error, token, "indeterminate store creation")
    require_order(
        device_error,
        ("result == FS_LOOKUP_INDETERMINATE",
         "agent_metadata_store_fail_closed_runtime()",
         "AGENT_METADATA_PERSIST_FAIL_CLOSED",
         "agent_meta_persist.irrevocable = 1", "return result;",
         "result == VIRTIO_DISK_ERR_BUSY"),
        "fail closed before ordinary device errors",
    )
    persist = function_body(store, "static int agent_file_persist(")
    start_failure = persist[persist.find("if (start_status < 0) {"):
                            persist.find("started_here = 1")]
    for token in ("agent_meta_persist.error_cause",
                  "failure_irrevocable = agent_meta_persist.irrevocable"):
        require(start_failure, token, "pre-job indeterminate completion")
    completion_update = persist[persist.rfind("\nout:"):]
    require(completion_update,
            "completion->irrevocable = failure_irrevocable;",
            "persist completion irrevocability")
    removed_put = function_body(fs, "static int fs_put_removed_checked(")
    for token in ("if (ip->ref != 1 || !ip->valid || !ip->removed) {\n"
                  "\t\tiput(ip);\n\t\treturn -1;\n\t}",
                  "inode_remove_detach(ip, &reclaim)",
                  "if (detached <= 0)\n\t\treturn -1;",
                  "return itruncate_reclaim(&reclaim)"):
        require(removed_put, token, "checked created-inode reclaim")
    discard = function_body(fs, "int fs_rollback_created_workflow(")
    for token in ("expected_dev == 0", "expected_inum == 0",
                  "expected_incarnation == 0", "vfs_scope_retained(scope_id)",
                  "dp->dev != expected_dev", "ip->dev != expected_dev",
                  "ip->inum != expected_inum",
                  "ip->vfs_incarnation != expected_incarnation",
                   "ip->agent_meta_slot != 0",
                   "ip->agent_meta_flags != 0",
                   "ip->agent_meta_version != 0",
                   "agent_edit_unlink_allowed(ip)",
                   "dirunlink(dp, path, offset, expected_inum, expected_incarnation",
                   "agent_edit_note_delete(ip)", "ip->removed = 1",
                   "status = fs_put_removed_checked(ip)", "return status;"):
        require(discard, token, "exact created-inode rollback")
    require_order(
        discard,
        ("dirlookup(dp, path", "ip->dev != expected_dev",
         "dirunlink(dp, path, offset, expected_inum, expected_incarnation",
          "ip->removed = 1", "status = fs_put_removed_checked(ip)"),
        "identity check before created-inode removal",
    )

    meta_set = function_body(objects, "int sys_agent_file_meta_set(")
    if meta_set.count("agent_metadata_catalog_mutation_begin(") != 1:
        raise ContractError("metadata mutation does not use one syscall fence")
    if meta_set.count("agent_metadata_catalog_undo_capture(") < 3:
        raise ContractError("post-commit/bind/delete states are not all bound")
    if meta_set.count("agent_metadata_catalog_mutation_end(") != 1:
        raise ContractError("metadata syscall lacks a single fence cleanup")
    require_order(meta_set,
                  ("agent_metadata_catalog_resolve(scope_id, &meta",
                   "agent_metadata_catalog_mutation_begin(",
                   "agent_metadata_catalog_clear_slot(slot)",
                   "agent_metadata_catalog_undo_capture("),
                  "write entry fence and delete token")
    set_start = meta_set.find("if (slot < 0) {")
    if set_start < 0:
        raise ContractError("metadata set allocation branch is missing")
    set_path = meta_set[set_start:]
    require_order(
        set_path,
        ("agent_metadata_catalog_alloc_slot(scope_id)",
         "result = agent_catalog_error_status(slot)",
         "agent_metadata_catalog_edit_begin("),
        "catalog allocation status before fenced edit",
    )
    set_positions = [
        set_path.find("agent_metadata_catalog_edit_begin("),
        set_path.find("agent_metadata_catalog_edit_commit("),
    ]
    first_capture = set_path.find(
        "agent_metadata_catalog_undo_capture(", set_positions[-1]
    )
    bind_pos = set_path.find("agent_metadata_catalog_bind(", first_capture)
    second_capture = set_path.find(
        "agent_metadata_catalog_undo_capture(", first_capture + 1
    )
    set_positions.extend((first_capture, bind_pos, second_capture))
    if any(pos < 0 for pos in set_positions) or set_positions != sorted(set_positions):
        raise ContractError("set fence and refreshed token: wrong order")
    require_order(
        set_path[bind_pos:],
        ("agent_metadata_catalog_bind(",
         "agent_metadata_catalog_undo_capture(",
         "agent_metadata_catalog_undo_note_created(",
         "agent_file_read_slot("),
        "bind receipt before fallible post-bind work",
    )
    for token in ("bind_status == AGENT_CATALOG_INDETERMINATE ?\n"
                  "\t\t\t\tAGENT_STATUS_INDETERMINATE : AGENT_STATUS_IO_ERROR",
                  "(bind_status > 0 && agent_metadata_catalog_undo_note_created(",
                  "agent_metadata_store_fail_closed_runtime()"):
        require(meta_set, token, "bind cleanup completion state")
    persist_status = function_body(objects, "agent_persist_agent_status(")
    require_order(persist_status,
                  ("if (persist->irrevocable)",
                   "return AGENT_STATUS_INDETERMINATE"),
                  "irrevocable persistence status")
    require_order(meta_set,
                  ("agent_file_finish_mutation(",
                   "agent_metadata_catalog_mutation_end(",
                   "agent_metadata_txn_unlock()"),
                  "single syscall cleanup")
    finish = function_body(objects, "agent_file_finish_mutation(")
    for token in ("const struct agent_catalog_mutation_fence *fence",
                  "const struct agent_catalog_undo_token *undo",
                  "agent_metadata_store_persist_commit(&persistence)",
                  "agent_file_restore_status(fence, undo"):
        require(finish, token, "fenced persistence rollback")
    reclaim = function_body(objects, "int agent_scope_reclaim_begin(")
    require_order(reclaim,
                  ("reclaimed = agent_metadata_catalog_reclaim_scope(scope_id)",
                   "if (reclaimed < 0)",
                   "agent_metadata_actions_reclaim_scope(scope_id)"),
                  "scope teardown defers on a foreign fence")

    txn_require = function_body(
        metadata, "agent_metadata_txn_require_owned("
    )
    for token in ("agent_metadata_txn_owned(exact_depth)",
                  'panic("%s", reason)'):
        require(txn_require, token, "shared transaction ownership assertion")
    batch = function_body(objects, "agent_file_update_status_batch_locked(")
    if batch.count("agent_metadata_txn_require_owned(") != 1:
        raise ContractError(
            "locked batch helper lacks one exact-depth transaction assertion"
        )
    require(batch,
            'agent_metadata_txn_require_owned(1, '
            '"Agent metadata action transaction")',
            "locked batch exact-depth ownership")
    for token in ("agent_metadata_txn_lock(",
                  "agent_metadata_txn_unlock("):
        if token in batch:
            raise ContractError(
                f"locked batch helper recursively manages transaction: {token}"
            )
    require_order(batch,
                  ("agent_metadata_txn_require_owned(1,",
                   "agent_metadata_store_submit_wait_locked()",
                   "agent_file_store_load()",
                   "agent_metadata_catalog_mutation_begin(",
                   "agent_metadata_actions_update_status_locked(",
                   "agent_metadata_catalog_mutation_end(",
                   "return updated;"),
                  "batch rollback fence lifetime")
    for token in ("AGENT_METADATA_PERSIST_RETRY",
                  "AGENT_METADATA_PERSIST_FAIL_CLOSED"):
        require(batch, token, "batch fence failure status")
    require_order(
        batch,
        ("memset(persist, 0, sizeof(*persist))", "persist->durable = 1",
         "agent_metadata_txn_require_owned(1,"),
        "non-null batch persistence receipt initialization",
    )
    update_call = function_body(objects, "static void agent_object_state_update(")
    require_order(
        update_call,
        ("updated = agent_file_update_status_batch_locked(", "&persistence)",
         "if (persistence.status < 0 || !persistence.durable)"),
        "batch persistence receipt ownership",
    )
    submit_start = batch.find("if (!agent_metadata_store_submit_wait_locked())")
    load_start = batch.find("load_status = agent_file_store_load()")
    require_order(
        batch[submit_start:load_start],
        ("persist->durable = 0", "persist->status = -1",
         "persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED", "return 0;"),
        "submit failure receipt before catalog load",
    )
    begin_start = batch.find("if (agent_metadata_catalog_mutation_begin(")
    require_order(
        batch[load_start:begin_start],
        ("load_status = agent_file_store_load()", "if (load_status < 0)",
         "return agent_metadata_load_agent_status(load_status)"),
        "catalog load ABI status before mutation fence",
    )
    update_start = batch.find("agent_metadata_actions_update_status_locked(")
    require_order(
        batch[begin_start:update_start],
        ("persist->durable = 0", "persist->status = -1",
         "persist->cause = AGENT_METADATA_PERSIST_RETRY", "return 0;"),
        "fence admission failure receipt",
    )
    end_start = batch.find("if (agent_metadata_catalog_mutation_end(")
    require_order(
        batch[end_start:],
        ("agent_metadata_store_fail_closed_runtime()", "persist->durable = 0",
         "persist->status = -1",
         "persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED",
         "persist->irrevocable = 1", "return updated;"),
        "fence completion failure receipt",
    )
    update = function_body(
        actions, "agent_metadata_actions_update_status_locked("
    )
    if "agent_metadata_store_submit_wait_locked(" in update:
        raise ContractError("status action helper waits on the metadata gate")
    rollback = function_body(actions, "agent_file_status_batch_rollback(")
    for token in (
        "char text[AGENT_FILE_FIELD_SIZE + AGENT_FILE_SUMMARY_SIZE]",
        "__builtin_offsetof(struct agent_file_meta, summary) ==",
        "__builtin_offsetof(struct agent_file_meta, status) +",
    ):
        require(actions, token, "status and summary undo extent")
    require(update, "memmove(undo->text, meta->status, sizeof(undo->text))",
            "status and summary capture")
    require(update, "!persist->irrevocable",
            "irrevocable batch rollback suppression")
    require(rollback,
            "memmove(edit.meta->status, undo->text, sizeof(undo->text))",
            "status and summary restore")
    require_order(update,
                  ("agent_metadata_catalog_edit_begin(",
                   "agent_metadata_store_persist_commit(persist)",
                   "agent_file_status_batch_rollback("),
                  "batch rollback under caller fence")

def load_sources(root: Path) -> dict[str, str]:
    paths = {
        "agent_h": root / "os/agent.h",
        "catalog": root / "os/agent_metadata_catalog.c",
        "catalog_h": root / "os/agent_metadata_catalog.h",
        "metadata": root / "os/agent_metadata.c",
        "objects": root / "os/agent_metadata_objects.c",
        "actions": root / "os/agent_metadata_actions.c",
        "fs": root / "os/fs.c",
        "fs_h": root / "os/fs.h",
        "file": root / "os/file.c",
        "store_io": root / "os/agent_metadata_store_io.c",
        "store": root / "os/agent_metadata_store.c",
        "vfs": root / "os/vfs_security.c",
    }
    return {name: path.read_text(encoding="utf-8")
            for name, path in paths.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        validate_sources(load_sources(args.root.resolve()))
    except (ContractError, OSError) as error:
        print(f"metadata catalog rollback fence contract failed: {error}")
        return 1
    print("metadata catalog rollback fence contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
