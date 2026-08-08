#!/usr/bin/env python3
"""固定元数据 catalog 分区与协调的静态契约。"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path
import re


class ContractError(RuntimeError):
    pass


@functools.lru_cache(maxsize=32)
def _code_mask(source: str) -> str:
    """遮蔽注释和字符串，同时保留源码下标。"""
    masked = list(source)
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and following == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                masked[index] = " "
                state = "string"
            elif char == "'":
                masked[index] = " "
                state = "character"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and following == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                masked[index] = " "
        else:
            if char == "\\" and following:
                masked[index] = " "
                if following != "\n":
                    masked[index + 1] = " "
                index += 2
                continue
            if ((state == "string" and char == '"') or
                    (state == "character" and char == "'")):
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        index += 1
    return "".join(masked)


def _balanced_end(source: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    for pos in range(start, len(source)):
        if source[pos] == opening:
            depth += 1
        elif source[pos] == closing:
            depth -= 1
            if depth == 0:
                return pos
    return -1


def function_body(source: str, signature: str) -> str:
    """按函数名提取定义，允许声明限定符和 GCC 属性发生变化。"""
    name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", signature)
    if name_match is None:
        raise ContractError(f"invalid function signature: {signature}")
    name = name_match.group(1)
    masked = _code_mask(source)
    candidate = re.compile(rf"\b{re.escape(name)}\s*\(")
    for match in candidate.finditer(masked):
        line_start = masked.rfind("\n", 0, match.start()) + 1
        if masked[line_start:match.start()].lstrip().startswith("#"):
            continue
        paren = masked.find("(", match.start())
        params_end = _balanced_end(masked, paren, "(", ")")
        if params_end < 0:
            continue
        pos = params_end + 1
        while True:
            while pos < len(masked) and masked[pos].isspace():
                pos += 1
            attribute = re.match(r"__attribute__?\b", masked[pos:])
            if attribute is None:
                break
            pos += attribute.end()
            while pos < len(masked) and masked[pos].isspace():
                pos += 1
            if pos >= len(masked) or masked[pos] != "(":
                break
            attribute_end = _balanced_end(masked, pos, "(", ")")
            if attribute_end < 0:
                break
            pos = attribute_end + 1
        while pos < len(masked) and masked[pos].isspace():
            pos += 1
        if pos >= len(masked) or masked[pos] != "{":
            continue
        body_end = _balanced_end(masked, pos, "{", "}")
        if body_end < 0:
            raise ContractError(f"unterminated body: {signature}")
        return source[match.start():body_end + 1]
    raise ContractError(f"missing function: {signature}")


def require(source: str, token: str, label: str) -> None:
    if token not in source:
        raise ContractError(f"{label}: missing {token}")


def require_order(source: str, tokens: tuple[str, ...], label: str) -> None:
    position = -1
    for token in tokens:
        position = source.find(token, position + 1)
        if position < 0:
            raise ContractError(f"{label}: missing or out of order {token}")


def validate_sources(sources: dict[str, str]) -> None:
    agent = sources["agent"]
    actions = sources["actions"]
    catalog = sources["catalog"]
    catalog_h = sources["catalog_h"]
    directory = sources["directory"]
    file_source = sources["file"]
    file_state = sources["file_state"]
    file_state_h = sources["file_state_h"]
    fs = sources["fs"]
    fs_program = sources["fs_program"]
    internal = sources["internal"]
    objects = sources["objects"]
    recovery = sources["recovery"]
    scan = sources["scan"]
    scan_h = sources["scan_h"]
    store = sources["store"]
    store_format = sources["store_format"]
    storage_policy = sources["storage_policy"]
    vfs = sources["vfs"]
    user = sources["user"]
    scope_program = sources["scope_program"]

    legacy_selector = re.compile(r"\bagent_metadata_catalog_find(?:_scan)?\s*\(")
    for name, source in sources.items():
        if legacy_selector.search(source):
            raise ContractError(
                f"catalog restored a duplicate full-table selector: {name}"
            )
    hidden_borrow = re.compile(r"\bagent_metadata_catalog_borrow_scan\s*\(")
    for name, source in sources.items():
        if name in {"catalog", "catalog_h", "scan", "kernel_other"}:
            continue
        if hidden_borrow.search(source):
            raise ContractError(f"scanner-only borrow escaped into {name}")
    if hidden_borrow.search(sources["kernel_other"]):
        raise ContractError("scanner-only borrow escaped outside catalog scanner")
    for name, expected in (("catalog", 2), ("catalog_h", 1), ("scan", 2)):
        if len(hidden_borrow.findall(sources[name])) != expected:
            raise ContractError(f"scanner-only borrow count changed in {name}")
    hidden_edit = re.compile(r"\bagent_metadata_catalog_edit_begin_scan\s*\(")
    for name, source in sources.items():
        if name in {"catalog", "catalog_h", "scan", "kernel_other"}:
            continue
        if hidden_edit.search(source):
            raise ContractError(f"scanner-only edit escaped into {name}")
    if hidden_edit.search(sources["kernel_other"]):
        raise ContractError("scanner-only edit escaped outside catalog scanner")
    for name in ("catalog", "catalog_h", "scan"):
        if len(hidden_edit.findall(sources[name])) != 1:
            raise ContractError(f"scanner-only edit count changed in {name}")

    for token in (
        "#define AGENT_FILE_META_MAX       512",
        "#define AGENT_FILE_SYSTEM_LIMIT   64",
        "#define AGENT_FILE_SCOPE_LIMIT    112",
        "(AGENT_FILE_META_MAX - AGENT_FILE_SYSTEM_LIMIT)",
        "#define AGENT_FILE_STATUS_BATCH_LIMIT 112",
    ):
        require(agent, token, "catalog partition")
    for forbidden in (
        "AGENT_FILE_SCOPE_GUARANTEE",
        "AGENT_FILE_SCOPE_BURST_LIMIT",
        "RESOURCE_AGENT_CATALOG",
        "agent_catalog_pressure_admissible",
        "agent_catalog_resource_",
        "vfs_scope_metadata_envelope",
    ):
        for name, source in sources.items():
            if forbidden in source:
                raise ContractError(
                    f"obsolete elastic catalog mechanism remains in {name}: "
                    f"{forbidden}"
                )
    for token in (
        "agent_catalog_files[AGENT_FILE_META_MAX]",
        "agent_catalog_scopes[AGENT_FILE_META_MAX]",
        "agent_catalog_states[AGENT_FILE_META_MAX]",
    ):
        require(catalog, token, "fixed catalog storage")
    if "agent_catalog_apply_slots" in catalog:
        raise ContractError("snapshot plans must not use a shared slot array")
    require(
        catalog,
        "VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT ==\n"
        "\t       AGENT_FILE_ORDINARY_LIMIT",
        "fixed ordinary partitions",
    )
    require(catalog_h, "#define AGENT_CATALOG_SCOPE_PLAN_MAX 8",
            "retained lifecycle scope accounting")
    for token in (
        "#define AGENT_FILE_EXPLICIT_RESERVE 16",
        "#define AGENT_FILE_AUTOSCAN_SCOPE_LIMIT \\\n"
        "\t(AGENT_FILE_SCOPE_LIMIT - AGENT_FILE_EXPLICIT_RESERVE)",
    ):
        require(catalog_h, token, "per-scope explicit metadata reserve")
    require(catalog,
            "VFS_SCOPE_LIFECYCLE_CAP == AGENT_CATALOG_SCOPE_PLAN_MAX",
            "retained lifecycle scope bound")
    require(
        catalog_h,
        "int agent_metadata_catalog_record_base_valid(\n"
        "\tconst struct agent_file_meta *, uint, uint);",
        "shared record validator declaration",
    )
    record_base = function_body(
        catalog, "agent_metadata_catalog_record_base_valid("
    )
    for token in (
        "meta != 0",
        "slot < AGENT_FILE_META_MAX",
        "meta->used == 1",
        "meta->fid > 0",
        "agent_object_scope_valid(scope_id)",
        "meta->physical_name[0] != 0",
        "meta->physical_name[sizeof(meta->physical_name) - 1] == 0",
        "meta->logical_path[sizeof(meta->logical_path) - 1] == 0",
        "meta->project[sizeof(meta->project) - 1] == 0",
        "meta->workflow[sizeof(meta->workflow) - 1] == 0",
        "meta->run_id[sizeof(meta->run_id) - 1] == 0",
        "meta->stage[sizeof(meta->stage) - 1] == 0",
        "meta->kind[sizeof(meta->kind) - 1] == 0",
        "meta->status[sizeof(meta->status) - 1] == 0",
        "meta->summary[sizeof(meta->summary) - 1] == 0",
        "meta->update_mask == 0",
        "meta->flags & AGENT_FILE_META_F_PERSIST",
        "meta->flags & ~(AGENT_FILE_META_F_PERSIST |",
    ):
        require(record_base, token, "shared record validator")
    for forbidden in (
        "agent_meta_record_strings_valid",
        "agent_meta_record_base_valid",
    ):
        if forbidden in store_format:
            raise ContractError(
                f"store format restored a second record authority: {forbidden}"
            )
    catalog_record = function_body(catalog, "agent_catalog_record_valid(")
    require(
        catalog_record,
        "agent_metadata_catalog_record_base_valid(",
        "catalog shared record validation",
    )
    require(catalog_record, "strlen(meta->physical_name) <= DIRSIZ",
            "exact DIRSIZ catalog record")
    normalize = function_body(
        catalog, "static void agent_catalog_normalize_physical("
    )
    require(normalize, "strlen(meta->physical_name) > DIRSIZ",
            "exact DIRSIZ physical-name normalization")
    if "agent_meta_format_" in catalog:
        raise ContractError("catalog introduced a reverse store-format dependency")
    resolver = function_body(catalog, "agent_metadata_catalog_resolve(")
    key_match = function_body(catalog, "agent_catalog_key_matches(")
    for token in ("agent_metadata_txn_work_charge(1)",
                  "agent_catalog_target_lifecycle(",
                  "agent_catalog_scope_counts(\n"
                  "\t\tscope_id, lifecycle, &owned, &autoscan)",
                  "result->ordinary = agent_catalog_ordinary",
                  "agent_catalog_primary_candidates(",
                  "agent_catalog_bitmap_next(candidates, -1)",
                  "slot == except_slot",
                  "result->matched |= matched",
                  "result->states |= agent_catalog_states[slot]",
                  "result->slot = AGENT_CATALOG_CONFLICT"):
        require(resolver, token, "incremental catalog selector resolver")
    if "for (int i = 0; i < AGENT_FILE_META_MAX" in resolver:
        raise ContractError("ordinary resolver restored a full catalog scan")
    require(catalog_h, "int slot, owned, ordinary, autoscan;",
            "per-scope autoscan accounting")
    for token in ("selector->fid", "selector->physical_name",
                  "selector->logical_path", "selector->dev",
                  "selector->inum", "selector->incarnation"):
        require(key_match, token, "four-key selector matching")
    for token in (
        "#define AGENT_CATALOG_KEY_PATH",
        "AGENT_CATALOG_KEY_PHYSICAL | AGENT_CATALOG_KEY_LOGICAL",
        "agent_metadata_catalog_identity_state(",
        "meta->dev != 0 && meta->inum != 0 && meta->incarnation != 0",
        "meta->dev == 0 && meta->inum == 0 && meta->incarnation == 0",
        "return present ? 1 : absent ? 0 : -1",
    ):
        require(catalog_h, token, "shared catalog identity classification")
    scope_admission = function_body(catalog, "agent_catalog_scope_admissible(")
    for token in ("vfs_scope_lifecycle(scope_id, lifecycle)",
                  "workflow_lifecycle_active(*lifecycle)",
                  "workflow_lifecycle_closing(*lifecycle)"):
        require(scope_admission, token, "trusted target lifecycle admission")
    scope_create = function_body(vfs, "static int vfs_scope_create(")
    for token in (
        "registry->active_count + registry->retiring_count <\n"
        "\t\t    VFS_SCOPE_MAX_ACTIVE",
        "registry->active_count + registry->retiring_count <\n"
        "\t\t    VFS_SCOPE_LIFECYCLE_CAP",
        "registry->free_count > 0",
        "vfs_scope_registry_insert_locked(scope_id, created, storage)",
    ):
        require(scope_create, token, "active/closing/retiring scope slots")
    if scope_create.find("registry->active_count + registry->retiring_count") > \
            scope_create.find("workflow_lifecycle_create(scope_id, &created)"):
        raise ContractError("retiring scopes must occupy admission slots")
    storage_guarantee = function_body(vfs, "vfs_scope_storage_guarantee(")
    for token in (
        "(!ref->retiring &&",
        "!workflow_lifecycle_key_valid(ref->lifecycle)",
        "allocated = registry->active_count + registry->retiring_count",
        "if (ref->scope_id == exempt_scope)",
        "used = resource_account_usage(",
        "inode ? RESOURCE_FS_INODE : RESOURCE_FS_BLOCK",
        "if (used < guarantee)",
        "required += guarantee - used",
        "if (allocated < VFS_SCOPE_MAX_ACTIVE)",
        "(VFS_SCOPE_MAX_ACTIVE - allocated) * guarantee",
    ):
        require(storage_guarantee, token, "retained-scope storage guarantee")
    if "!ref->used || ref->retiring ||" in storage_guarantee:
        raise ContractError("retiring scope was skipped by storage guarantee")
    retained_pos = storage_guarantee.find("(!ref->retiring &&")
    allocated_pos = storage_guarantee.find(
        "allocated = registry->active_count + registry->retiring_count"
    )
    exempt_pos = storage_guarantee.find("if (ref->scope_id == exempt_scope)")
    usage_pos = storage_guarantee.find("used = resource_account_usage(")
    deficit_pos = storage_guarantee.find("required += guarantee - used")
    future_pos = storage_guarantee.find(
        "(VFS_SCOPE_MAX_ACTIVE - allocated) * guarantee"
    )
    if not (0 <= allocated_pos < retained_pos < exempt_pos < usage_pos <
            deficit_pos < future_pos):
        raise ContractError(
            "retiring/active usage must offset its slot before future guarantees"
        )
    hard_admission = function_body(
        catalog, "static int agent_catalog_hard_admission("
    )
    for token in ("agent_metadata_catalog_resolve(scope_id, candidate, except_slot",
                  "result->matched", "AGENT_CATALOG_CONFLICT",
                  "AGENT_FILE_SCOPE_LIMIT", "result->owned >= limit",
                  "result->ordinary >= AGENT_FILE_ORDINARY_LIMIT"):
        require(hard_admission, token, "hard catalog admission")
    if "AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" in hard_admission:
        raise ContractError("hard admission depends on the live autoscan policy")
    admission = function_body(catalog, "static int agent_catalog_admission(")
    for token in ("agent_catalog_hard_admission(", "old_flags", "flags",
                  "flags & AGENT_FILE_META_F_AUTOSCAN",
                  "!(old_flags & AGENT_FILE_META_F_AUTOSCAN)",
                  "scope_id != VFS_SCOPE_SYSTEM",
                  "result.autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT",
                  "if (!growth || scope_id == VFS_SCOPE_SYSTEM)",
                  "agent_catalog_scope_admissible(scope_id, &lifecycle)",
                  "return AGENT_CATALOG_INTERRUPTED"):
        require(admission, token, "live catalog admission")
    require(
        admission,
        "if (scope_id != VFS_SCOPE_SYSTEM &&\n"
        "\t    (flags & AGENT_FILE_META_F_AUTOSCAN) &&\n"
        "\t    !(old_flags & AGENT_FILE_META_F_AUTOSCAN)",
        "exact autoscan delta admission",
    )
    hard_pos = admission.find("agent_catalog_hard_admission(")
    autoscan_pos = admission.find(
        "result.autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT"
    )
    bypass_pos = admission.find("if (!growth || scope_id == VFS_SCOPE_SYSTEM)")
    lifecycle_pos = admission.find("agent_catalog_scope_admissible(")
    if not (0 <= hard_pos < autoscan_pos < bypass_pos < lifecycle_pos):
        raise ContractError(
            "hard capacity and autoscan delta must precede lifecycle admission"
        )
    require(catalog, "scope_id != VFS_SCOPE_SYSTEM", "SYSTEM partition")
    commit = function_body(catalog, "int agent_metadata_catalog_edit_commit(")
    for token in ("!agent_catalog_files[edit->slot].used",
                  "admission = agent_catalog_admission(",
                  "edit->scope_id, edit->slot, edit->meta,",
                  "growth ? 0 : agent_catalog_files[edit->slot].flags",
                  "edit->meta->flags, growth)",
                  "admission <= 0", "AGENT_CATALOG_CONFLICT"):
        require(commit, token, "authoritative commit admission")
    normalize_pos = commit.find("agent_catalog_normalize_physical(edit->slot, edit->meta)")
    admission_pos = commit.find("growth = !agent_catalog_files[edit->slot].used")
    if not (0 <= normalize_pos < admission_pos):
        raise ContractError("physical key normalization must precede commit admission")
    require(commit, "agent_catalog_scopes[edit->slot] != edit->scope_id",
            "immutable record scope")
    allocate = function_body(catalog, "int agent_metadata_catalog_alloc_slot(")
    for token in ("agent_catalog_admission(scope_id, -1, 0, 0, flags, 1)",
                  "agent_catalog_bitmap_next(agent_catalog_free_bits, -1)",
                  "agent_metadata_txn_work_charge(1)"):
        require(allocate, token, "candidate-bound slot allocation")
    require(catalog_h, "int agent_metadata_catalog_alloc_slot(uint, uint);",
            "flag-aware allocator declaration")
    restore = function_body(catalog, "int agent_metadata_catalog_restore(")
    require(restore, "agent_catalog_hard_admission(",
            "receipt-authorized hard rollback")
    if "agent_catalog_admission(" in restore or \
            "AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" in restore:
        raise ContractError("exact undo restore depends on live soft admission")
    if "agent_catalog_admission_topology" in catalog + catalog_h:
        raise ContractError("catalog restored a duplicate lifecycle topology")
    key_start = catalog_h.find("struct agent_catalog_plan_key {")
    key_end = catalog_h.find("};", key_start)
    if key_start < 0 or key_end < 0:
        raise ContractError("catalog plan key declaration missing")
    key_decl = catalog_h[key_start:key_end]
    for token in (
        "const struct agent_meta_record *records;",
        "uint64 candidate_epoch, catalog_generation, lifecycle_generation;",
        "uint count, reload_scope;",
        "int reload_one_scope;",
        "uint lifecycle_id;",
    ):
        require(key_decl, token, "scalar lifecycle plan binding")
    for token in ("struct workflow_lifecycle_key", "reserved", "topology"):
        if token in key_decl:
            raise ContractError(
                "raw plan key contains lifecycle padding or duplicate topology"
            )
    plan_key = function_body(catalog, "agent_catalog_plan_key(")
    for token in ("memset(&key, 0, sizeof(key))",
                  "key.lifecycle_id = lifecycle.id",
                  "key.lifecycle_generation = lifecycle.generation"):
        require(plan_key, token, "canonical scalar lifecycle binding")
    plan_count = function_body(catalog, "agent_catalog_plan_count(")
    for token in ("AGENT_FILE_SYSTEM_LIMIT", "AGENT_FILE_ORDINARY_LIMIT",
                  "AGENT_FILE_SCOPE_LIMIT",
                  "result->plan_scope_counts[scope_index]"):
        require(plan_count, token, "hard snapshot bounds")
    if "AUTOSCAN" in plan_count or "flags" in plan_count:
        raise ContractError("durable snapshot uses the current autoscan policy")
    if "plan_scope_autoscan_counts" in catalog_h:
        raise ContractError("snapshot retained redundant autoscan policy state")
    if "agent_catalog_scope_admissible(" in plan_count:
        raise ContractError("full boot plan counting gained live lifecycle policy")
    prepare = function_body(catalog, "agent_metadata_catalog_prepare_snapshot(")
    for token in ("struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none()",
                  "reload_one_scope &&\n\t    !agent_catalog_scope_admissible(reload_scope, &lifecycle)",
                  "agent_catalog_generation, lifecycle",
                  "result->plan_lifecycle_id != lifecycle.id",
                  "result->plan_lifecycle_generation != lifecycle.generation",
                  "AGENT_METADATA_LOAD_INTERRUPTED"):
        require(prepare, token, "scoped reload lifecycle-bound prepare")
    require(prepare, "agent_catalog_plan_count(result, record->scope_id) < 0",
            "snapshot representation bounds")
    lifecycle_check = prepare.find("agent_catalog_scope_admissible(")
    plan_key_build = prepare.find("key = agent_catalog_plan_key(")
    lifecycle_revalidate = prepare.find(
        "result->plan_lifecycle_generation != lifecycle.generation"
    )
    if not (0 <= lifecycle_check < plan_key_build < lifecycle_revalidate):
        raise ContractError(
            "scoped prepare must bind and revalidate immutable lifecycle identity"
        )
    record_lifecycle = prepare.find("vfs_scope_lifecycle(record->scope_id")
    missing_record = prepare.find(
        "agent_catalog_bit_set(result->missing_slots", record_lifecycle
    )
    count_record = prepare.find("agent_catalog_plan_count(", record_lifecycle)
    if not (0 <= record_lifecycle < missing_record < count_record):
        raise ContractError(
            "cold boot must discard stale lifecycle records before capacity admission"
        )
    if "AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" in prepare:
        raise ContractError("snapshot prepare treats a live soft limit as corruption")
    apply = function_body(catalog, "agent_metadata_catalog_apply_snapshot(")
    for token in ("if (reload_one_scope &&\n\t    (!agent_catalog_scope_admissible(reload_scope, &lifecycle)",
                  "!agent_catalog_scope_admissible(reload_scope, &lifecycle)",
                  "result->plan_lifecycle_id != lifecycle.id",
                  "result->plan_lifecycle_generation != lifecycle.generation",
                  "agent_catalog_generation, lifecycle",
                  "AGENT_METADATA_LOAD_INTERRUPTED",
                  'panic("Agent catalog apply binding invariant")'):
        require(apply, token, "scoped reload lifecycle recheck")
    lifecycle_check = apply.find("agent_catalog_scope_admissible(")
    plan_key_build = apply.find("key = agent_catalog_plan_key(")
    projection_begin = apply.find("agent_metadata_txn_projection_begin()")
    if not (0 <= lifecycle_check < plan_key_build < projection_begin):
        raise ContractError(
            "scoped lifecycle generation must be rechecked before apply"
        )
    allocate_fid = function_body(catalog, "uint64 agent_metadata_catalog_alloc_fid(")
    for token in ("agent_catalog_target_lifecycle(-1, scope_id, &lifecycle)",
                  "agent_catalog_usage_find(scope_id, lifecycle, 0)",
                  "~usage->fids[word]",
                  "agent_catalog_first_bit(candidates) + 1",
                  "agent_metadata_txn_work_charge(1)"):
        require(allocate_fid, token, "incremental fid allocation")
    if "agent_catalog_files[" in allocate_fid:
        raise ContractError("fid allocation restored a catalog scan")
    restart_validator = function_body(
        store_format, "records_valid(struct agent_meta_store *store"
    )
    for token in (
        "agent_metadata_catalog_record_base_valid(",
        "workflow_lifecycle_key_equal(",
        "workflow_lifecycle_none()",
        "workflow_lifecycle_key_valid(lifecycle)",
        "lifecycle.id > WORKFLOW_LIFECYCLE_CAP",
        "AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT",
        "++ordinary > AGENT_FILE_ORDINARY_LIMIT",
        "prior->slot == record->slot",
        "prior->meta.fid == record->meta.fid",
        "prior->meta.physical_name",
        "(record->meta.logical_path[0] &&",
        "prior->meta.logical_path",
        "prior->meta.dev == record->meta.dev",
        "prior->meta.inum == record->meta.inum",
        "prior->meta.incarnation == record->meta.incarnation",
    ):
        require(restart_validator, token, "shared restart record validation")
    if "AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" in restart_validator:
        raise ContractError(
            "raw validation must leave stale-scope filtering to lifecycle projection"
        )
    for token in ("&store->records[i]", "&store->records[j]"):
        require(restart_validator, token, "current record layout binding")
    current_validator = function_body(
        store_format, "agent_meta_format_records_valid("
    )
    for token in (
        "agent_durable_arena_validate(&store->durable) >= 0",
        "records_valid(store)",
    ):
        require(current_validator, token, "current/v7 restart validation binding")
    if "v5" in store_format:
        raise ContractError("retired metadata v5 decoder was restored")

    account = function_body(fs, "int fs_storage_scope_account_create(")
    for token in (
        "RESOURCE_FS_INODE",
        "fs_storage.workflow_inode_domain_limit;",
    ):
        require(account, token, "independent workflow inode account limit")
    if "AGENT_FILE_SCOPE_LIMIT" in fs:
        raise ContractError("filesystem source was recoupled to catalog capacity")
    require(storage_policy, "#define FS_WORKFLOW_INODE_MIN_PER_SCOPE 320U",
            "workflow inode capacity floor")

    status_select = function_body(actions, "agent_status_select(")
    for token in (
        "count >= AGENT_FILE_STATUS_BATCH_LIMIT",
        "return AGENT_STATUS_NO_SPACE",
        "(selected[i / 8] & bit) == 0",
    ):
        require(status_select, token, "bounded status selection")
    status_update = function_body(
        actions, "agent_metadata_actions_update_status_locked("
    )
    select_end = status_update.rfind("agent_status_select(")
    edit_start = status_update.find("agent_metadata_catalog_edit_begin(")
    if not (0 <= select_end < edit_start):
        raise ContractError("status selection must reject overflow before mutation")
    for token in (
        "agent_status_batch_undo[AGENT_FILE_STATUS_BATCH_LIMIT]",
        "if (primary_updated < 0)",
        "if (selected_count < 0)",
    ):
        require(actions, token, "bounded status rollback")
    require(objects, "if (updated < 0)", "status batch error propagation")

    for token in (
        "#define AGENT_CATALOG_STALE       -2",
        "#define AGENT_CATALOG_CONFLICT    -3",
        "#define AGENT_CATALOG_INDETERMINATE -4",
        "#define AGENT_CATALOG_NO_SPACE    -5",
        "#define AGENT_CATALOG_INTERRUPTED -6",
    ):
        require(catalog_h, token, "structured catalog errors")
    error_status = function_body(objects, "agent_catalog_error_status(")
    for token in (
        "case AGENT_CATALOG_NO_SPACE:",
        "return AGENT_STATUS_NO_SPACE",
        "case AGENT_CATALOG_INTERRUPTED:",
        "case AGENT_CATALOG_STALE:",
        "return AGENT_STATUS_RETRY",
        "case AGENT_CATALOG_CONFLICT:",
        "return AGENT_STATUS_CONFLICT",
        "case AGENT_CATALOG_INDETERMINATE:",
        "return AGENT_STATUS_INDETERMINATE",
        "default:",
        "return AGENT_STATUS_IO_ERROR",
    ):
        require(error_status, token, "catalog-to-Agent ABI status mapping")
    meta_set = function_body(objects, "int sys_agent_file_meta_set(uint64 metaaddr)")
    for token in (
        "slot = agent_metadata_catalog_alloc_slot(scope_id, 0)",
        "result = agent_catalog_error_status(slot)",
        "commit_status = agent_metadata_catalog_edit_commit(&edit, changes)",
        "result = agent_catalog_error_status(commit_status)",
    ):
        require(meta_set, token, "catalog error propagation to Agent ABI")
    if meta_set.count("result = agent_catalog_error_status(commit_status)") != 2:
        raise ContractError("clear and edit commit must both preserve catalog errors")

    create = function_body(fs, "struct inode *fs_create(")
    if not (create.find("dirlookup(") < create.find("ialloc(") < create.find("dirlink(")):
        raise ContractError("create ordering must lookup, reserve inode, then publish")
    if "agent_metadata_catalog" in create or "agent_file_request_scan" in create:
        raise ContractError("VFS create became conditional on metadata catalog capacity")
    canonicalize = function_body(fs, "int fs_dirent_canonicalize(")
    for token in (
        "input == 0 || out == 0 || input[0] == 0",
        "memset(out, 0, DIRSIZ + 1)",
        "i < DIRSIZ && input[i] != 0",
        "out[i] = input[i]",
    ):
        require(canonicalize, token, "single bounded dirent canonicalizer")
    if "strlen(" in canonicalize:
        raise ContractError("dirent canonicalization must not reject legacy long aliases")
    for signature, argument, first_effect in (
        ("struct inode *dirlookup(", "name", "vfs_cred_kernel("),
        ("int dirlink(", "name", "vfs_inode_authorize("),
        ("int dirunlink(", "name", "vfs_inode_authorize("),
        ("int fs_rollback_created_workflow(", "path", "vfs_cred_kernel("),
        ("struct inode *fs_create(", "path", "dp = root_dir_status("),
    ):
        body = function_body(fs, signature)
        require_order(
            body,
            (f"fs_dirent_canonicalize({argument}, key)", first_effect),
            f"{signature} canonical dirent key",
        )
    for signature, token in (
        ("struct inode *dirlookup(", "strncmp(key, de.name, DIRSIZ) == 0"),
        ("int dirlink(", "strncmp(key, de.name, DIRSIZ) != 0"),
        ("int dirunlink(", "strncmp(key, de.name, DIRSIZ) != 0"),
        ("int fs_rollback_created_workflow(", "dirlookup(dp, key,"),
        ("struct inode *fs_create(", "dirlookup(dp, key,"),
    ):
        require(function_body(fs, signature), token,
                f"{signature} uses canonical dirent key")
    lookup = function_body(fs, "struct inode *dirlookup(")
    require_order(
        lookup,
        ("strncmp(key, de.name, DIRSIZ) == 0", "target = inode_get(",
         "target->vfs_policy != policy", "target->vfs_scope_id != scope_id"),
        "canonical aliases preserve inode policy and scope checks",
    )
    note_create = function_body(directory, "void agent_fs_note_create(")
    require_order(
        note_create,
        ("fs_dirent_canonicalize(path, key)",
         "agent_metadata_scan_index_inode(ip, key, &failed)"),
        "create metadata uses the published canonical dirent key",
    )
    fs_name_test = function_body(
        fs_program, "static void check_dirent_name_boundary(")
    for token in (
        'const char *canonical = "nmlimit1234567";',
        'const char *long_name = "nmlimit1234567x";',
        'const char *same_alias = "nmlimit1234567y";',
        "open(long_name, O_CREATE | O_RDWR | O_TRUNC)",
        "append through long-name create",
        "same prefix reopens one legacy alias",
        "query_physical(canonical)",
        "agent_file_meta_init() == AGENT_STATUS_OK",
        "canonical metadata delete remains durable",
        "dirent_name_bound=14 legacy_alias=1 metadata_canonical=1",
    ):
        require(fs_name_test, token, "Guest dirent-name boundary regression")
    require(function_body(fs_program, "static void run_agent("),
            "check_dirent_name_boundary();",
            "Guest dirent-name boundary execution")
    ialloc = function_body(fs, "struct inode *ialloc(")
    if not (ialloc.find("fs_storage_reserve(charge, 1)") <
            ialloc.find("dip->type = type")):
        raise ContractError("inode lease must precede persistent allocation intent")
    require(ialloc, "fs_storage_release(charge->owner, 1)", "allocation refund")

    require(file_state_h, "#define AGENT_INODE_META_DEFERRED_SLOT (-1)",
            "persistent capacity-deferred marker")
    for token in (
        "dip->agent_meta_slot = ip->agent_meta_slot",
        "ip->agent_meta_slot = dip->agent_meta_slot",
    ):
        require(fs, token, "deferred marker disk round-trip")
    deferred_state = function_body(file_state, "agent_file_state_index_deferred(")
    for token in (
        "ip->agent_meta_slot == AGENT_INODE_META_DEFERRED_SLOT",
        "ip->agent_meta_flags == 0",
        "ip->agent_meta_version == AGENT_INODE_META_VERSION",
    ):
        require(deferred_state, token, "unambiguous deferred sidecar state")
    set_index = function_body(file_state, "agent_file_state_set_index(")
    for token in (
        "!stale && ip->agent_meta_slot > 0",
        "short version = slot ? AGENT_INODE_META_VERSION : 0",
        "slot < AGENT_INODE_META_DEFERRED_SLOT",
        "slot > AGENT_FILE_META_MAX",
        "slot <= 0 && flags",
        "ip->agent_meta_slot = slot",
        "ip->agent_meta_flags = flags",
        "ip->agent_meta_version = version",
        "if (iupdate(ip) >= 0",
    ):
        require(set_index, token, "durable sidecar update")
    require(file_state_h,
            "int agent_file_state_set_index(struct inode *, short, short, int);",
            "shared sidecar updater declaration")
    for name, source in sources.items():
        if name in {"file_state", "file_state_h", "catalog", "scan", "kernel_other"}:
            continue
        if "agent_file_state_set_index(" in source:
            raise ContractError(
                f"sidecar update escaped into {name}"
            )

    note_create = function_body(directory, "void agent_fs_note_create(")
    for token in (
        "agent_metadata_txn_try_external()",
        "agent_metadata_store_loaded()",
        "agent_file_state_content_bump(ip)",
        "agent_metadata_scan_index_inode(ip, key, &failed)",
        "agent_metadata_note_catalog_changes(changes)",
        "agent_file_state_index_deferred(ip)",
        "agent_file_request_scan()",
    ):
        require(note_create, token, "shared VFS create indexing")
    index_pos = note_create.find("agent_metadata_scan_index_inode(ip, key, &failed)")
    changes_pos = note_create.find("agent_metadata_note_catalog_changes(changes)")
    deferred_pos = note_create.find("agent_file_state_index_deferred(ip)")
    schedule_scan = note_create.find("agent_file_request_scan()", index_pos)
    if not (0 <= index_pos < changes_pos < deferred_pos < schedule_scan):
        raise ContractError(
            "catalog saturation can roll back VFS create or strand deferred state"
        )
    fileopen = function_body(file_source, "int fileopen(")
    require(fileopen, "if (created)\n\t\tagent_fs_note_create(ip, path)",
            "post-create metadata hook")
    fs_create_pos = fileopen.find("ip = fs_create(")
    note_create_pos = fileopen.find("agent_fs_note_create(ip, path)")
    success_pos = fileopen.find("return fd", note_create_pos)
    if not (0 <= fs_create_pos < note_create_pos < success_pos):
        raise ContractError("catalog hook can still roll back successful VFS creation")

    publish_content = function_body(directory, "agent_fs_publish_content(")
    receipt_note = "agent_metadata_catalog_journal_note_content(&receipt)"
    if publish_content.count(receipt_note) != 1:
        raise ContractError("ordinary writes lost their bounded journal receipt")
    guarded_content = publish_content.replace(receipt_note, "")
    if ("agent_metadata_txn_" in guarded_content or
            "agent_metadata_catalog_" in guarded_content):
        raise ContractError("ordinary writes still enter catalog transactions")
    for token in (
        "agent_file_state_content_publish(ip, &receipt)",
        "agent_metadata_store_mark_dirty(ip->vfs_scope_id)",
        "agent_file_request_scan()",
    ):
        require(publish_content, token, "content overlay fast path")

    remove_inode = function_body(directory, "agent_fs_remove_inode(")
    # 稀疏版本表把 deferred 归入所有未发布的 slot；删除路径必须先按
    # 完整 inode 身份回收版本项，再请求重扫，不能进入 catalog 事务。
    deferred_pos = remove_inode.find("ip->agent_meta_slot <= 0")
    reclaim_pos = remove_inode.find("agent_file_version_reclaim(ip)", deferred_pos)
    txn_pos = remove_inode.find("agent_metadata_txn_try_external()")
    rescan_pos = remove_inode.find("agent_file_request_scan()", reclaim_pos)
    return_pos = remove_inode.find("return;", rescan_pos)
    if not (
        0 <= deferred_pos < reclaim_pos < rescan_pos < return_pos < txn_pos
    ):
        raise ContractError("deferred delete does not reconcile before catalog work")
    busy_scan_pos = remove_inode.find("agent_file_request_scan()", txn_pos)
    busy_return_pos = remove_inode.find("return;", busy_scan_pos)
    if not (txn_pos < busy_scan_pos < busy_return_pos):
        raise ContractError("metadata-gate busy delete can strand catalog cleanup")
    if "agent_metadata_scan_slot_freed" in remove_inode[
            txn_pos:busy_return_pos]:
        raise ContractError("metadata-gate busy delete claims unreleased capacity")
    clear_pos = remove_inode.find("agent_metadata_catalog_clear_slot(slot)")
    retry_pos = remove_inode.find(
        "agent_metadata_scan_slot_freed(scope_id)", clear_pos)
    unlock_pos = remove_inode.find("agent_metadata_txn_unlock()", retry_pos)
    if not (0 <= clear_pos < retry_pos < unlock_pos):
        raise ContractError("catalog capacity release can strand deferred inodes")
    require(scan_h, "void agent_metadata_scan_slot_freed(uint);",
            "capacity-release retry declaration")
    retry_deferred = function_body(scan, "agent_metadata_scan_slot_freed(")
    for token in (
        "if (!scan_scope_full(scope, 0))",
        "scan_ctl.on = 1",
        "scan_ctl.pending = SCAN_URGENT",
        "scan.next_tick = agent_file_state_now()",
        "agent_background_request()",
    ):
        require(retry_deferred, token, "urgent deferred retry")
    pause = function_body(scan, "scan_pause(")
    require_order(
        pause,
        ("if (scan_ctl.pending == SCAN_URGENT)", "scan.start = 0",
         "scan_ctl.pending = 1", "scan.next_tick = current",
         "} else {", "if (retry) {",
         "if (!resume || scan_ctl.pending > 0)",
         "else if (scan_ctl.pending == 0)",
         "scan_ctl.pending = -1", "if (scan.next_tick < now)"),
        "urgent full restart must outrank cursor resume and bypass one rest window",
    )
    scan_remove = function_body(scan, "scan_remove(")
    require_order(
        scan_remove,
        ("agent_metadata_catalog_clear_slot(slot)",
         "agent_metadata_scan_slot_freed(scope)",
         "agent_metadata_store_mark_dirty(scope)"),
        "stale sweep releases capacity before scheduling a deferred retry",
    )
    require(scan, "uint marked[SCOPE_MAX * 2], nmarked;",
            "per-scan scope outcome cache")
    marked = function_body(scan, "scan_mark(")
    for token in ("FS_OWNER_SCOPE_FLAG", "scan.nmarked >= NELEM(scan.marked)",
                  "scan.marked[scan.nmarked++] = mark"):
        require(marked, token, "bounded scope cache")
    require(scan,
            "scan_scope_full(scope, add) scan_mark(scope, add, 1)",
            "bounded saturated-scope cache")
    require(scan, "uchar seen[AGENT_META_STALE_BYTES]", "bounded scan bitmap")
    require(scan, "SCAN_BIND_DEFERRED", "capacity failure class")
    if scan.count("*failed = SCAN_BIND_DEFERRED;") != 1:
        raise ContractError("slot and fid exhaustion must share one deferral path")
    require(scan, "slot < 0 || !(fid = agent_metadata_catalog_alloc_fid(scope))",
            "shared slot/fid deferral")
    fid_pos = scan.find(
        "slot < 0 || !(fid = agent_metadata_catalog_alloc_fid(scope))")
    fid_mark_pos = scan.find("scan_scope_full(scope, 1)", fid_pos)
    fid_defer_pos = scan.find("*failed = SCAN_BIND_DEFERRED", fid_mark_pos)
    if not (0 <= fid_pos < fid_mark_pos < fid_defer_pos):
        raise ContractError("fid exhaustion lacks a scoped deferred hint")
    require(scan, "scan.deferred++", "deferred observation")
    require(scan, "scan.failures++", "failure observation")
    require(scan, "scan_pause(1, 1)", "resumable root scan")
    require(scan, "scan.offset = off;\n\t\t\tscan_pause(1, 1);",
            "resumable directory read")
    require(scan, "scan_scope_failed(view.scope_id, 0)", "scope isolation")
    require(scan, "file_scan_deferred = scan.deferred", "deferred ABI")
    require(scan, "file_scan_failures = scan.failures", "failure ABI")

    quota_wait = function_body(scope_program, "wait_quota_catalog_rebuild(")
    for token in (
        "scope_catalog_path_count(name)",
        "info.file_scan_deferred > deferred_before",
        "!info.file_scan_pending",
        "!info.metadata_writeback_pending",
        "info.metadata_writeback_dirty ==\n\t\t\tinfo.metadata_writeback_durable",
        "stable >= 3",
    ):
        require(quota_wait, token, "Guest deferred catalog rebuild")
    quota_fill = function_body(scope_program, "fill_scope_storage_quota(")
    quota_order = (
        "check_quota_catalog_path_count('a', WORKFLOW_AUTOSCAN_SCOPE_LIMIT, 0)",
        "state->explicit_created = create_explicit_metadata(",
        "unlink(released_name)",
        "state->first_removed = 1",
        "wait_quota_catalog_rebuild(",
        "agent_file_meta_init() == 0",
        "catalog_deferred_rebuilt=1",
    )
    position = -1
    for token in quota_order:
        next_position = quota_fill.find(token, position + 1)
        if next_position < 0:
            raise ContractError(
                f"Guest deferred catalog rebuild is incomplete: {token}"
            )
        position = next_position
    quota_cleanup = function_body(scope_program, "cleanup_scope_storage_quota(")
    require(
        quota_cleanup,
        "remove_quota_files_from('a', state->first_removed, state->created)",
        "Guest released-slot cleanup",
    )
    for token in (
        "SCAN_DEFAULT(physical_name, AGENT_FILE_CHANGE_SCOPE_KEYS, 0)",
        "SCAN_DEFAULT(logical_path, AGENT_FILE_CHANGE_SCOPE_KEYS, 0)",
        'SCAN_DEFAULT(project, AGENT_FILE_CHANGE_SCOPE_KEYS, "root")',
        'SCAN_DEFAULT(workflow, AGENT_FILE_CHANGE_SCOPE_KEYS, "background-scan")',
        'SCAN_DEFAULT(run_id, AGENT_FILE_CHANGE_SCOPE_KEYS, "ROOT")',
        'SCAN_DEFAULT(stage, AGENT_FILE_CHANGE_STAGE, "scan")',
        'SCAN_DEFAULT(summary, 0, "auto scanned root file")',
    ):
        require(scan, token, "table-driven scan defaults")
    defaults = function_body(scan, "agent_metadata_scan_apply_defaults(")
    for token in (
        "i < NELEM(scan_default_fields)",
        "rule->value ? rule->value : path",
        "changes |= rule->changes",
        "scan_infer(path, meta->kind",
        "scan_infer(path, meta->status",
    ):
        require(defaults, token, "table-driven scan defaults")
    step = function_body(scan, "agent_metadata_scan_step(uint64 now")
    if "scan_failed" in step or "if (bind_failed)\n\t\t\t\tbreak" in step:
        raise ContractError("one object can still abort the global scan")
    for token in ("scan.retry = scan.sweep_uncertain = 1;\n\t\t\tcontinue;",
                  "(void)scan_scope_failed(ip->vfs_scope_id, 1);"):
        require(step, token, "fair object continuation")

    for header in (agent, user):
        require(header, "uint64 file_scan_deferred;", "scan ABI")
        require(header, "uint64 file_scan_failures;", "scan ABI")
    for token in (
        "#define AGENT_CATALOG_STATE_PENDING",
        "#define AGENT_CATALOG_STATE_QUARANTINE",
        "struct agent_catalog_plan_key",
        "const struct agent_meta_record *plan_records;",
        "plan_candidate_epoch, plan_catalog_generation",
        "plan_token, plan_hash",
        "plan_cursor, plan_catalog_cursor",
        "pending_slots[AGENT_META_STALE_BYTES]",
        "quarantine_slots[AGENT_META_STALE_BYTES]",
    ):
        require(catalog_h, token, "candidate-owned projection plan")
    require(internal, "AGENT_METADATA_LOAD_PROGRESS = -5",
            "bounded prepare status")
    plan_key = function_body(catalog, "agent_catalog_plan_key(")
    for token in ("key.records = records", "key.count = count",
                  "key.reload_one_scope = reload_one_scope",
                  "key.reload_scope = reload_scope",
                  "key.candidate_epoch = candidate_epoch",
                  "key.catalog_generation = catalog_generation"):
        require(plan_key, token, "immutable candidate plan key")
    plan_binding = function_body(catalog, "agent_catalog_plan_binding(")
    for token in ("agent_catalog_hash_bytes", "sizeof(*key)"):
        require(plan_binding, token, "full plan-key token binding")
    prepare = function_body(catalog, "agent_metadata_catalog_prepare_snapshot(")
    for token in (
        "candidate_epoch == 0",
        "result->plan_key = key",
        "agent_catalog_plan_key(records, count, reload_one_scope",
        "key.catalog_generation = result->plan_catalog_generation",
        "memcmp(&result->plan_key, &key, sizeof(key)) != 0",
        "result->plan_token = agent_catalog_plan_binding(",
        "result->plan_hash = agent_catalog_hash_bytes(",
        "result->plan_catalog_generation != agent_catalog_generation",
        "AGENT_METADATA_LOAD_INTERRUPTED",
        "AGENT_CATALOG_PREPARE_STEP",
        "reload_one_scope ? count : AGENT_CATALOG_PREPARE_STEP",
        "AGENT_METADATA_LOAD_PROGRESS",
        "agent_metadata_catalog_identity_state(&record->meta)",
        "result->quarantine_slots",
        "result->pending_slots",
        "if (identity == 0)\n\t\t\t\tagent_catalog_bit_set(result->missing_slots,",
    ):
        require(prepare, token, "pure-memory candidate prepare")
    for forbidden in (
        "namei_scope_status(", "agent_catalog_lookup_or_create_status(",
        "inode_get(", "ivalid(", "iupdate(", "fs_read_", "readi(",
    ):
        if forbidden in prepare:
            raise ContractError(
                f"candidate prepare contains filesystem I/O: {forbidden}"
            )
    abort = function_body(catalog, "agent_metadata_catalog_prepare_abort(")
    require(abort, "memset(result, 0, sizeof(*result))", "plan abort")

    borrow = function_body(catalog, "agent_metadata_catalog_borrow(")
    for token in ("agent_metadata_catalog_borrow_scan(slot, view)",
                  "view->state != 0", "view->meta = 0"):
        require(borrow, token, "ordinary hidden-state visibility")
    borrow_scan = function_body(catalog, "agent_metadata_catalog_borrow_scan(")
    require(borrow_scan, "view->state = agent_catalog_states[slot]",
            "scanner-only visibility")
    unbind = function_body(catalog, "static int agent_catalog_unbind(int slot")
    guard = unbind.find("AGENT_CATALOG_STATE_QUARANTINE")
    lookup = unbind.find("namei_scope_status(")
    if guard < 0 or lookup < 0 or guard > lookup:
        raise ContractError("zero/quarantine unbind guard must precede pathname lookup")
    require(unbind, "agent_file_state_set_index(ip, 0, 0, 0)",
            "shared catalog sidecar clear")
    bind_status = function_body(catalog, "static int agent_catalog_bind_status(")
    if "agent_catalog_normalize_physical(" in bind_status:
        raise ContractError("bind mutates the physical key after commit admission")
    require(bind_status, "strlen(meta->physical_name) > DIRSIZ",
            "exact DIRSIZ catalog binding")
    require(bind_status, "agent_file_state_set_index(",
            "shared catalog sidecar bind")
    rollback_created = function_body(fs, "int fs_rollback_created_workflow(")
    require(rollback_created, "fs_dirent_canonicalize(path, key) < 0",
            "canonical creation rollback")
    reconcile = function_body(catalog, "agent_metadata_catalog_reconcile_slot(")
    for token in ("AGENT_CATALOG_STATE_PENDING",
                  "agent_catalog_slot_lifecycle(slot)",
                  "agent_catalog_slot_publish(\n"
                  "\t\tslot, &agent_catalog_files[slot], scope_id, 0, lifecycle,",
                  "AGENT_FILE_CHANGE_MEMBERSHIP"):
        require(reconcile, token, "pending reconciliation")
    publish = function_body(catalog, "agent_catalog_slot_publish(")
    for token in ("agent_catalog_derived_remove(slot)",
                  "agent_catalog_derived_add(",
                  "agent_catalog_changed(changes)",
                  "agent_catalog_journal_note(old_scope, old_lifecycle, slot)"):
        require(publish, token, "single catalog publication boundary")

    apply = function_body(catalog, "agent_metadata_catalog_apply_snapshot(")
    for token in (
        "agent_catalog_plan_key(records, count, reload_one_scope",
        "memcmp(&result->plan_key, &key, sizeof(key)) != 0",
        "panic(\"Agent catalog apply binding invariant\")",
        "hash != result->plan_hash",
        "panic(\"Agent catalog apply plan invariant\")",
    ):
        require(apply, token, "exact apply binding")
    projection_pos = apply.find("agent_metadata_txn_projection_begin()")
    if projection_pos < 0:
        raise ContractError("catalog apply never begins projection")
    projection_tail = apply[projection_pos:]
    for token in (
        "agent_metadata_catalog_clear_slot(",
        "agent_catalog_bind_status(",
        "agent_catalog_unbind(",
        "iupdate(",
        "return AGENT_METADATA_LOAD_", "return -",
    ):
        if token in projection_tail:
            raise ContractError(
                f"catalog projection tail contains fallible operation: {token}"
            )
    if projection_tail.count("return ") != 1 or "return result->used;" not in projection_tail:
        raise ContractError("catalog projection must have one infallible success return")

    admission = function_body(objects, "agent_metadata_admission_status(void)")
    require(admission, "agent_metadata_catalog_reconcile_pending()",
            "global admission readiness")
    require(scan, "agent_metadata_catalog_borrow_scan", "scanner hidden borrow")
    require(scan, "AGENT_CATALOG_STATE_QUARANTINE", "quarantine scan policy")
    require(scan, "agent_metadata_catalog_reconcile_slot(slot)",
            "pending scan policy")
    bind_scan = function_body(scan, "agent_metadata_scan_index_inode(struct inode *ip")
    if "if (failed)" in bind_scan:
        raise ContractError("scanner bind failure output became optional")
    require(bind_scan, "*failed = 0", "scanner bind failure output")
    require(
        bind_scan,
        "scope == VFS_SCOPE_SYSTEM ? mut || !exec_policy_inode_trusted(ip) :",
        "trusted SYSTEM scan admission",
    )
    require(bind_scan, "slot = ip->agent_meta_slot - 1",
            "scanner sidecar hint")
    deferred_guard = (
        "if (agent_file_state_index_deferred(ip) &&\n"
        "\t    scan_scope_full(scope, 0)) {\n"
        "\t\tscan.deferred++;\n"
        "\t\treturn 0;\n"
        "\t}"
    )
    for token in (
        deferred_guard,
        "stale_sidecar = ip->agent_meta_slot > 0",
        "agent_metadata_catalog_alloc_slot(",
        "scope, AGENT_FILE_META_F_AUTOSCAN)",
        "if (slot == AGENT_CATALOG_NO_SPACE)",
        "scan_scope_full(scope, 1)",
        "AGENT_INODE_META_DEFERRED_SLOT, 0, stale_sidecar) < 0",
        "ip->agent_meta_flags != persist",
        "agent_file_state_set_index(ip, slot + 1, persist, 0) < 0",
    ):
        require(bind_scan, token, "durable capacity deferral")
    ordinary_pos = bind_scan.find("agent_metadata_catalog_borrow(0, slot, &view)")
    stale_pos = bind_scan.find("stale_sidecar = ip->agent_meta_slot > 0")
    allocate_pos = bind_scan.find("agent_metadata_catalog_alloc_slot(")
    no_space_pos = bind_scan.find("if (slot == AGENT_CATALOG_NO_SPACE)")
    saturate_pos = bind_scan.find("scan_scope_full(scope, 1)")
    persist_pos = bind_scan.find("AGENT_INODE_META_DEFERRED_SLOT, 0, stale_sidecar)")
    return_pos = bind_scan.find("return changes", persist_pos)
    if not (0 <= ordinary_pos < stale_pos < allocate_pos < no_space_pos <
            saturate_pos < persist_pos < return_pos):
        raise ContractError("scanner deferral is not checked and persisted in order")
    persist_value_pos = bind_scan.find(
        "persist = meta->flags & AGENT_FILE_META_F_PERSIST"
    )
    sidecar_flag_pos = bind_scan.find("ip->agent_meta_flags != persist")
    sidecar_write_pos = bind_scan.find(
        "agent_file_state_set_index(ip, slot + 1, persist, 0)"
    )
    if not (0 <= persist_value_pos < sidecar_flag_pos < sidecar_write_pos):
        raise ContractError(
            "scanner sidecar persist flag is not derived, compared, and repaired in order"
        )
    for token in (
        "memset(&selector, 0, sizeof(selector))",
        "safestrcpy(selector.physical_name, path,",
        "safestrcpy(selector.logical_path, path,",
        "selector.dev = ip->dev",
        "selector.inum = ip->inum",
        "selector.incarnation = ip->vfs_incarnation",
        "agent_metadata_catalog_resolve(scope, &selector, -1, &resolution)",
        "resolution.slot == AGENT_CATALOG_CONFLICT",
        "resolution.matched & AGENT_CATALOG_KEY_IDENTITY",
        "resolution.matched & AGENT_CATALOG_KEY_PATH",
    ):
        require(bind_scan, token, "shared scanner selector")
    identity_only = (
        "if (resolution.slot == AGENT_CATALOG_CONFLICT ||\n"
        "\t\t    ((resolution.matched & AGENT_CATALOG_KEY_IDENTITY) != 0 &&\n"
        "\t\t     (resolution.matched & AGENT_CATALOG_KEY_PATH) == 0))\n"
        "\t\t\tgoto retry;"
    )
    require(bind_scan, identity_only, "scanner split-key conflict mapping")
    require(
        bind_scan,
        "slot = (resolution.matched & AGENT_CATALOG_KEY_PATH) != 0 ?\n"
        "\t\t\t       resolution.slot : -1;",
        "scanner path-authoritative mapping",
    )
    find_pos = bind_scan.find(
        "agent_metadata_catalog_resolve(scope, &selector, -1, &resolution)"
    )
    hidden_pos = bind_scan.find("agent_metadata_catalog_borrow_scan(")
    if not (0 <= ordinary_pos < find_pos < hidden_pos):
        raise ContractError(
            "scanner hidden borrow escaped the identity-bound pathname fallback"
        )
    if bind_scan.count("agent_metadata_catalog_borrow_scan(") != 1:
        raise ContractError("scanner hidden borrow escaped its unique fallback")
    require(bind_scan, "agent_metadata_catalog_borrow_scan(slot, &view)",
            "scanner fallback slot binding")
    conflict_pos = bind_scan.find(
        "if (resolution.slot == AGENT_CATALOG_CONFLICT"
    )
    revalidate_pos = bind_scan.find(
        "agent_metadata_catalog_identity_state(view.meta) < 0"
    )
    if not (find_pos < conflict_pos < hidden_pos < revalidate_pos):
        raise ContractError("scanner fallback is not conflict-safe and revalidated")
    for token in (
        "strncmp(view.meta->physical_name, path,",
        "strncmp(view.meta->logical_path, path,",
        "sizeof(view.meta->physical_name)) != 0",
        "sizeof(view.meta->logical_path)) != 0",
    ):
        require(bind_scan, token,
                "scanner fallback post-selection revalidation")
    replacement = (
        "if (slot >= 0 && view.meta->dev &&\n"
        "\t    !scan_matches(view.meta, ip)) {"
    )
    require(bind_scan, replacement, "scanner incarnation replacement")
    if "!scan_matches(view.meta, ip) &&" in bind_scan:
        raise ContractError("scanner replacement retained a flag-based exception")
    remove_pos = bind_scan.find("scan_remove(slot, old_scope, old_persist)")
    fresh_pos = bind_scan.find("slot = -1", remove_pos)
    alloc_pos = bind_scan.find("agent_metadata_catalog_alloc_slot(")
    if not (revalidate_pos < remove_pos < fresh_pos < alloc_pos):
        raise ContractError("scanner replacement can inherit an old object identity")
    quarantine_pos = bind_scan.find("AGENT_CATALOG_STATE_QUARANTINE")
    edit_pos = bind_scan.find("agent_metadata_catalog_edit_begin_scan(")
    if not (0 <= quarantine_pos < edit_pos):
        raise ContractError("quarantine can reach the scanner edit path")
    step_scan = function_body(scan, "agent_metadata_scan_step(uint64 now")
    require(step_scan, "scan.nmarked = 0", "per-run scope outcome reset")
    dirent_name = (
        "char name[DIRSIZ + 1]"
    )
    require(step_scan, dirent_name, "scanner bounded dirent name")
    dirent_copy_pos = step_scan.find("memmove(name, de.name, DIRSIZ)")
    dirent_nul_pos = step_scan.find("name[DIRSIZ] = 0")
    bind_inode_pos = step_scan.find(
        "agent_metadata_scan_index_inode(ip, name, &bind_failed)"
    )
    if not (0 <= dirent_copy_pos < dirent_nul_pos < bind_inode_pos):
        raise ContractError("scanner fallback path is not the bounded dirent name")
    for token in ("agent_metadata_catalog_borrow_scan(i, &view)",
                  "view.state & AGENT_CATALOG_STATE_QUARANTINE"):
        require(step_scan, token, "quarantine-preserving stale sweep")
    meta_set = function_body(objects, "int sys_agent_file_meta_set(uint64 metaaddr)")
    for token in ("agent_metadata_catalog_resolve(scope_id, &meta, -1, &selector)",
                  "selector.states & AGENT_CATALOG_STATE_PENDING",
                  "selector.slot == AGENT_CATALOG_CONFLICT",
                  "selector.matched != selector.provided",
                  "AGENT_CATALOG_KEY_IDENTITY",
                  "AGENT_STATUS_RETRY : AGENT_STATUS_CONFLICT"):
        require(meta_set, token, "unified selector status")

    load = function_body(store, "agent_file_load_snapshot(int force,")
    for token in (
        "&agent_meta_workspace.load.result",
        "agent_meta_store_apply_prepare(",
        "result = agent_metadata_catalog_apply_snapshot(",
        "reload_scope, candidate_epoch, apply",
        "if (result != AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
    ):
        require(load, token, "persistent snapshot plan")
    require_order(
        load,
        (
            "agent_metadata_probe_finish(candidate_epoch)",
            "agent_meta_reconcile_required = 1",
            "agent_background_request()",
            "agent_meta_store_io_leave()",
        ),
        "complete snapshot reconciliation",
    )
    reconcile_start = load.find("agent_metadata_probe_finish(candidate_epoch)")
    reconcile_end = load.find("agent_meta_store_io_leave()", reconcile_start)
    reconcile_path = load[reconcile_start:reconcile_end]
    if re.search(r"\b(?:if|for|while|switch)\s*\(", reconcile_path):
        raise ContractError(
            "complete snapshot reconciliation is conditionally gated"
        )
    if "struct agent_metadata_apply_result apply;" in load:
        raise ContractError("load projection plan is still stack-local")
    store_prepare = function_body(store, "agent_meta_store_apply_prepare(")
    for token in (
        "apply->plan_candidate_epoch != candidate_epoch",
        "apply->plan_count != store->header.count",
        "apply->plan_records != agent_meta_bank_shadow[bank].records",
        "agent_meta_store_apply_abort()",
        "candidate_epoch, apply",
    ):
        require(store_prepare, token, "candidate epoch workspace")
    retryable = function_body(
        recovery, "agent_metadata_recovery_retryable(int status)"
    )
    for token in (
        "status == AGENT_METADATA_LOAD_INTERRUPTED",
        "status == AGENT_METADATA_LOAD_BUSY",
        "status == AGENT_METADATA_LOAD_IO",
    ):
        require(retryable, token, "structured transient recovery")
    for signature in (
        "agent_metadata_background_maintain(void)",
        "agent_metadata_tick(uint64 now)",
    ):
        consumer = function_body(objects, signature)
        require(
            consumer,
            "agent_metadata_store_take_reconcile_request())\n\t\tagent_file_request_scan();",
            "post-load reconciliation",
        )


def load_sources(root: Path) -> dict[str, str]:
    paths = {
        "agent": root / "os/agent.h",
        "actions": root / "os/agent_metadata_actions.c",
        "catalog": root / "os/agent_metadata_catalog.c",
        "catalog_h": root / "os/agent_metadata_catalog.h",
        "directory": root / "os/agent_metadata_directory.c",
        "file": root / "os/file.c",
        "file_state": root / "os/agent_file_state.c",
        "file_state_h": root / "os/agent_file_state_internal.h",
        "fs": root / "os/fs.c",
        "fs_program": root / "user/src/agentfs_ucore.c",
        "internal": root / "os/agent_metadata_internal.h",
        "objects": root / "os/agent_metadata_objects.c",
        "recovery": root / "os/agent_metadata_recovery.c",
        "scan": root / "os/agent_metadata_scan.c",
        "scan_h": root / "os/agent_metadata_scan.h",
        "store": root / "os/agent_metadata_store.c",
        "store_format": root / "os/agent_metadata_store_format.c",
        "storage_policy": root / "fs_storage_policy.h",
        "vfs": root / "os/vfs_security.c",
        "user": root / "user/include/agent.h",
        "scope_program": root / "user/src/agentscope_ucore.c",
    }
    sources = {
        name: path.read_text(encoding="utf-8") for name, path in paths.items()
    }
    allowed = {
        (root / "os/agent_metadata_catalog.c").resolve(),
        (root / "os/agent_metadata_catalog.h").resolve(),
        (root / "os/agent_metadata_scan.c").resolve(),
        (root / "os/agent_metadata_scan.h").resolve(),
    }
    kernel_sources = sorted(
        [*(root / "os").glob("*.c"), *(root / "os").glob("*.h")]
    )
    sources["kernel_other"] = "\n".join(
        f"/* {path.name} */\n{path.read_text(encoding='utf-8')}"
        for path in kernel_sources
        if path.resolve() not in allowed
    )
    return sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        validate_sources(load_sources(args.root.resolve()))
    except (ContractError, OSError) as error:
        print(f"metadata catalog capacity contract failed: {error}")
        return 1
    print("metadata catalog capacity contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
