#!/usr/bin/env python3
"""Mutation-test the bounded, fail-closed metadata re-probe mechanism."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "bio_h": ROOT / "os/bio.h",
    "internal": ROOT / "os/agent_metadata_internal.h",
    "core_internal": ROOT / "os/agent_internal.h",
    "probe_h": ROOT / "os/agent_metadata_probe.h",
    "probe": ROOT / "os/agent_metadata_probe.c",
    "io_h": ROOT / "os/agent_metadata_store_io.h",
    "io": ROOT / "os/agent_metadata_store_io.c",
    "recovery": ROOT / "os/agent_metadata_recovery.c",
    "profile_h": ROOT / "os/agent_metadata_recovery_test.h",
    "profile": ROOT / "os/agent_metadata_recovery_test.c",
    "persist_profile_h": ROOT / "os/metadata_crash_test.h",
    "persist_profile": ROOT / "os/agent_metadata_test.c",
    "agent_h": ROOT / "os/agent.h",
    "catalog_h": ROOT / "os/agent_metadata_catalog.h",
    "catalog": ROOT / "os/agent_metadata_catalog.c",
    "file_state_h": ROOT / "os/agent_file_state_internal.h",
    "file_state": ROOT / "os/agent_file_state.c",
    "scan": ROOT / "os/agent_metadata_scan.c",
    "vfs": ROOT / "os/vfs_security.c",
    "store": ROOT / "os/agent_metadata_store.c",
    "objects": ROOT / "os/agent_metadata_objects.c",
    "core": ROOT / "os/agent_core.c",
    "make": ROOT / "Makefile",
    "user_make": ROOT / "user/Makefile",
    "program": ROOT / "user/src/agentmetatransient_ucore.c",
    "large_program": ROOT / "user/src/agentmetalarge_ucore.c",
    "runner": ROOT / "scripts/run-metadata-recovery-tests.sh",
}
_NAMED_PATHS = set(FILES.values())
for _source_path in sorted((ROOT / "os").glob("*.c")):
    if _source_path not in _NAMED_PATHS:
        FILES[f"os_source:{_source_path.name}"] = _source_path


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    require(start >= 0, f"missing function: {signature}")
    opening = source.find("{", start)
    require(opening >= 0, f"missing function body: {signature}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise ContractError(f"unterminated function: {signature}")


def declaration_body(source: str, signature: str) -> str:
    start = source.find(signature)
    require(start >= 0, f"missing declaration: {signature}")
    opening = source.find("{", start)
    require(opening >= 0, f"unterminated declaration: {signature}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise ContractError(f"unterminated declaration: {signature}")


def require_order(source: str, tokens: tuple[str, ...], label: str) -> None:
    position = -1
    for token in tokens:
        next_position = source.find(token, position + 1)
        require(next_position >= 0, f"{label} missing ordered token: {token}")
        position = next_position


def require_guarded(source: str, needle: str, macro: str) -> None:
    """Require every source-level reference to be under a named #ifdef."""
    stack: list[str] = []
    found = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#ifdef "):
            stack.append(stripped.removeprefix("#ifdef ").strip())
        elif stripped.startswith("#ifndef "):
            stack.append("!" + stripped.removeprefix("#ifndef ").strip())
        elif stripped.startswith("#if defined(") and stripped.endswith(")"):
            stack.append(stripped[len("#if defined(") : -1].strip())
        elif stripped.startswith("#if"):
            stack.append(stripped)
        elif stripped.startswith("#elif"):
            require(bool(stack), "unbalanced preprocessor elif")
            stack[-1] = stripped
        elif stripped.startswith("#else"):
            require(bool(stack), "unbalanced preprocessor else")
            stack[-1] = (
                stack[-1][1:] if stack[-1].startswith("!") else "!" + stack[-1]
            )
        elif stripped.startswith("#endif"):
            require(bool(stack), "unbalanced preprocessor guard")
            stack.pop()
        if needle in line:
            found += 1
            require(macro in stack, f"unguarded production test reference: {needle}")
    require(found > 0, f"missing guarded reference: {needle}")


def validate(sources: dict[str, str]) -> None:
    bio_h = sources["bio_h"]
    internal = sources["internal"]
    core_internal = sources["core_internal"]
    probe_h = sources["probe_h"]
    probe = sources["probe"]
    io_h = sources["io_h"]
    io = sources["io"]
    recovery = sources["recovery"]
    profile_h = sources["profile_h"]
    profile = sources["profile"]
    persist_profile_h = sources["persist_profile_h"]
    persist_profile = sources["persist_profile"]
    agent_h = sources["agent_h"]
    catalog_h = sources["catalog_h"]
    catalog = sources["catalog"]
    file_state_h = sources["file_state_h"]
    file_state = sources["file_state"]
    scan = sources["scan"]
    vfs = sources["vfs"]
    store = sources["store"]
    objects = sources["objects"]
    core = sources["core"]
    makefile = sources["make"]
    user_make = sources["user_make"]
    program = sources["program"]
    large_program = sources["large_program"]
    runner = sources["runner"]

    # The public load-result vocabulary must distinguish bounded progress from
    # device/scheduler failures and from confirmed corruption.
    for token in (
        "AGENT_METADATA_LOAD_CORRUPT = -1",
        "AGENT_METADATA_LOAD_INTERRUPTED = -2",
        "AGENT_METADATA_LOAD_BUSY = -3",
        "AGENT_METADATA_LOAD_IO = -4",
        "AGENT_METADATA_LOAD_PROGRESS = -5",
    ):
        require(token in internal, f"load status lost: {token}")

    # Kernel load outcomes have one Agent ABI mapping. Callers may still use
    # NO_SPACE for transaction admission, but never as a catch-all load error.
    load_mapping = compact(
        function_body(objects, "agent_metadata_load_agent_status(")
    )
    require(
        "returnload_status==AGENT_METADATA_LOAD_INTERRUPTED||"
        "load_status==AGENT_METADATA_LOAD_BUSY||"
        "load_status==AGENT_METADATA_LOAD_PROGRESS?"
        "AGENT_STATUS_RETRY:AGENT_STATUS_IO_ERROR;" in load_mapping,
        "metadata load status no longer has one fail-closed Agent ABI mapping",
    )
    require(
        "AGENT_STATUS_NO_SPACE" not in load_mapping,
        "metadata load status is collapsed into NO_SPACE",
    )

    meta_init = compact(function_body(objects, "int sys_agent_file_meta_init("))
    require_order(
        meta_init,
        (
            "loaded=agent_file_store_reload(agent_identity_proc_scope(p))",
            "if(loaded<0)",
            "result=agent_metadata_load_agent_status(loaded)",
            "gotoout_txn",
            "agent_metadata_actions_clear_history(",
        ),
        "scoped metadata reload status mapping",
    )
    meta_init_load = meta_init[
        meta_init.find("loaded=agent_file_store_reload(") :
        meta_init.find("agent_metadata_actions_clear_history(")
    ]
    require(
        "AGENT_STATUS_NO_SPACE" not in meta_init_load,
        "scoped metadata reload failure is collapsed into NO_SPACE",
    )

    meta_set = compact(function_body(objects, "int sys_agent_file_meta_set("))
    require_order(
        meta_set,
        (
            "commit_status=agent_file_store_load()",
            "if(commit_status<0)",
            "result=agent_metadata_load_agent_status(commit_status)",
            "gotoout_txn",
            "agent_metadata_catalog_resolve(",
        ),
        "metadata set pre-load status mapping",
    )
    meta_set_load = meta_set[
        meta_set.find("commit_status=agent_file_store_load()") :
        meta_set.find("agent_metadata_catalog_resolve(")
    ]
    require(
        "AGENT_STATUS_NO_SPACE" not in meta_set_load,
        "metadata set pre-load failure is collapsed into NO_SPACE",
    )

    query = compact(function_body(objects, "agent_file_query_internal("))
    require_order(
        query,
        (
            "result=agent_file_store_load()",
            "if(result<0)",
            "result=agent_metadata_load_agent_status(result)",
            "gotoout_txn",
            "agent_metadata_query_execute_locked(",
        ),
        "metadata query load status mapping",
    )
    query_load = query[
        query.find("result=agent_file_store_load()") :
        query.find("agent_metadata_query_execute_locked(")
    ]
    require(
        "AGENT_STATUS_NO_SPACE" not in query_load,
        "metadata query load failure is collapsed into NO_SPACE",
    )

    execute_tool = compact(
        function_body(objects, "agent_metadata_execute_tool(")
    )
    tool_init = execute_tool[
        execute_tool.find("caseAGENT_TOOL_FILE_META_INIT:") :
        execute_tool.find("caseAGENT_TOOL_READ_FILE_SUMMARY:")
    ]
    require_order(
        tool_init,
        (
            "res->value0=agent_file_store_load()",
            "if((long)res->value0<0)",
            "agent_result_status(res,agent_metadata_load_agent_status("
            "(int)(long)res->value0),\"metadata_unavailable\")",
            "break",
            "agent_file_request_scan()",
        ),
        "FILE_META_INIT tool load status mapping",
    )
    require(
        "AGENT_STATUS_NO_SPACE" not in tool_init,
        "FILE_META_INIT load failure is collapsed into NO_SPACE",
    )

    # Every foreground catalog lookup must classify a failed load before it
    # can inspect the old in-memory catalog or reinterpret absence as success.
    require(
        objects.count("agent_file_store_load()") == 9,
        "metadata load call inventory changed without classification review",
    )
    file_find = compact(function_body(objects, "static int agent_file_find("))
    require_order(
        file_find,
        (
            "result=agent_file_store_load()",
            "if(result<0)",
            "returnagent_metadata_load_agent_status(result)",
            "for(inti=0;i<AGENT_FILE_META_MAX;i++)",
            "returnAGENT_STATUS_NOT_FOUND",
        ),
        "summary and plain digest lookup load status mapping",
    )

    summary = compact(function_body(objects, "agent_file_summary_read("))
    require_order(
        summary,
        (
            "slot=agent_file_find(scope_id,selector)",
            "if(slot<0)",
            "returnslot",
            "agent_file_read_slot(slot,&view)",
        ),
        "summary lookup status propagation",
    )
    summary_tool = execute_tool[
        execute_tool.find("caseAGENT_TOOL_READ_FILE_SUMMARY:") :
        execute_tool.find("caseAGENT_TOOL_READ_FILE_DIGEST:")
    ]
    require_order(
        summary_tool,
        (
            "found=agent_file_summary_read(",
            "if(found<0)",
            "agent_result_status(res,found,found==AGENT_STATUS_NOT_FOUND?"
            '"summary_not_found":"metadata_unavailable")',
        ),
        "summary tool load status propagation",
    )

    digest_select = compact(
        function_body(objects, "static int agent_file_digest_select(")
    )
    require_order(
        digest_select,
        (
            "found=agent_file_store_load()",
            "if(found<0)",
            "returnagent_metadata_load_agent_status(found)",
            "for(inti=0;i<AGENT_FILE_META_MAX;i++)",
            "found=agent_file_find(scope_id,selector)",
            "if(found<0&&found!=AGENT_STATUS_NOT_FOUND)",
            "returnfound",
            "safestrcpy(physical,selector,n)",
        ),
        "digest selector load status propagation",
    )
    digest_read = compact(function_body(objects, "agent_file_digest_read("))
    require(
        "rc==AGENT_STATUS_NOT_FOUND?\"digest_not_found\":"
        "rc==AGENT_STATUS_BAD_PARAM?\"bad_selector\":\"metadata_unavailable\""
        in digest_read,
        "digest load failure is presented as selector absence or syntax failure",
    )

    dependency_tool = execute_tool[
        execute_tool.find("caseAGENT_TOOL_DEPENDENCY_QUERY:") :
        execute_tool.find("caseAGENT_TOOL_DEPENDENCY_UPDATE:")
    ]
    require_order(
        dependency_tool,
        (
            "agent_parse_selector(op->payload,&dependency)",
            "found=agent_file_store_load()",
            "if(found<0)",
            "agent_result_status(res,agent_metadata_load_agent_status(found),"
            '"metadata_unavailable")',
            "break",
            "agent_metadata_actions_dependency_query(",
        ),
        "dependency query load status mapping",
    )
    dependency_load = dependency_tool[
        dependency_tool.find("found=agent_file_store_load()") :
        dependency_tool.find("agent_metadata_actions_dependency_query(")
    ]
    require(
        "AGENT_STATUS_NO_SPACE" not in dependency_load,
        "dependency query load failure is collapsed into NO_SPACE",
    )

    status_batch = compact(
        function_body(objects, "agent_file_update_status_batch_locked(")
    )
    require_order(
        status_batch,
        (
            "load_status=agent_file_store_load()",
            "if(load_status<0)",
            "returnagent_metadata_load_agent_status(load_status)",
            "agent_metadata_catalog_mutation_begin(",
        ),
        "status batch load status propagation",
    )
    state_update = compact(
        function_body(objects, "static void agent_object_state_update(")
    )
    require_order(
        state_update,
        (
            "updated=agent_file_update_status_batch_locked(",
            "if(updated<0)",
            "agent_result_status(res,updated,",
            '"metadata_unavailable"',
            "return",
            "if(updated==0)",
            "AGENT_STATUS_NOT_FOUND",
        ),
        "status batch error before true not-found mapping",
    )

    for token in (
        "AGENT_META_BANK_VALID 0",
        "AGENT_META_BANK_ABSENT 1",
        "AGENT_META_BANK_CORRUPT AGENT_METADATA_LOAD_CORRUPT",
        "AGENT_META_BANK_INTERRUPTED AGENT_METADATA_LOAD_INTERRUPTED",
        "AGENT_META_BANK_BUSY AGENT_METADATA_LOAD_BUSY",
        "AGENT_META_BANK_IO AGENT_METADATA_LOAD_IO",
        "AGENT_META_BANK_PROGRESS AGENT_METADATA_LOAD_PROGRESS",
        "AGENT_META_BANK_UNCOMMITTED 2",
        "void agent_metadata_probe_catalog_progress(int, uint);",
    ):
        require(token in probe_h, f"probe status mapping lost: {token}")

    # Probe, rather than the generic store-I/O lock owner, owns resumable bank
    # reads. This keeps cursor lifetime and the bytes it validates together.
    require(
        "agent_meta_store_io_read_bank" not in io_h + io,
        "legacy store_io bank reader still owns recovery policy",
    )
    cursor = compact(declaration_body(probe, "struct agent_metadata_probe_cursor"))
    for token in (
        "intactive;",
        "intbank;",
        "intconfirm;",
        "uintphase;",
        "uintoffset;",
        "uintstore_bytes;",
        "uint64dev;",
        "uint64inum;",
        "uint64incarnation;",
        "uint64inode_size;",
        "structagent_meta_store_headerheader;",
        "structagent_meta_store_headerverify_header;",
    ):
        require(token in cursor, f"probe cursor binding lost: {token}")

    # Scheduling checkpoints use a typed result so they cannot alias device or
    # filesystem integer errors.  The shared predicate deliberately treats
    # every state except READY as a stop; this keeps future states fail-closed.
    checkpoint_result = compact(
        declaration_body(bio_h, "struct bio_checkpoint_result")
    )
    require(
        "enumbio_checkpoint_statestate;" in checkpoint_result,
        "checkpoint result lost its typed state",
    )
    checkpoint_stop = compact(
        function_body(bio_h, "bio_checkpoint_should_stop(")
    )
    require(
        checkpoint_stop == "returnresult.state!=BIO_CHECKPOINT_READY;",
        "checkpoint stop predicate is not fail-closed",
    )

    piece = compact(function_body(probe, "agent_metadata_probe_piece("))
    require_order(
        piece,
        (
            "while(probe.cursor.offset<length)",
            "readi_device(ip,cred,0,(uint64)(dst+probe.cursor.offset),file_offset+probe.cursor.offset,length-probe.cursor.offset)",
            "structbio_checkpoint_resultcheckpoint",
            "if(n==VIRTIO_DISK_ERR_BUSY)returnAGENT_META_BANK_BUSY",
            "if(n<0)returnAGENT_META_BANK_IO",
            "if(n==0||(uint)n>length-probe.cursor.offset)returnAGENT_META_BANK_CORRUPT",
            "probe.cursor.offset+=n",
            "probe.progress_sequence++",
            "checkpoint=agent_metadata_txn_checkpoint_unlocked()",
            "if(!agent_metadata_reload_is_current()||!agent_meta_store_io_owned())",
            "returnAGENT_META_BANK_INTERRUPTED",
            "if(bio_checkpoint_should_stop(checkpoint))",
            "returncheckpoint.state==BIO_CHECKPOINT_DEFERRED?AGENT_META_BANK_PROGRESS:AGENT_META_BANK_INTERRUPTED",
        ),
        "resumable probe read",
    )
    require(
        "BIO_CHECKPOINT_INTERRUPTED" not in piece,
        "probe enumerates stop states instead of failing closed",
    )

    bind = compact(function_body(probe, "agent_metadata_probe_bind("))
    require(
        "if(probe.epoch!=0&&"
        "probe.key.authority_cookie==key->authority_cookie&&"
        "probe.key.reload_scope==key->reload_scope&&"
        "probe.key.force==key->force&&"
        "probe.key.resumable==key->resumable)return;" in bind,
        "probe operation-key equality guard is weakened",
    )
    for token in (
        "probe.key.authority_cookie==key->authority_cookie",
        "probe.key.reload_scope==key->reload_scope",
        "probe.key.force==key->force",
        "probe.key.resumable==key->resumable",
        "probe.key=*key",
        "agent_metadata_probe_new_epoch()",
    ):
        require(token in bind, f"probe operation-key binding lost: {token}")

    read = compact(function_body(probe, "agent_metadata_probe_read("))
    require_order(
        read,
        (
            "agent_metadata_probe_bind(key)",
            "if(!confirm&&probe.summary[bank].classified)",
            "returnprobe.summary[bank].status",
            "agent_metadata_probe_open(bank,&ip)",
        ),
        "terminal summary cache",
    )
    for token in (
        "probe.cursor.bank=bank",
        "probe.cursor.confirm=confirm",
        "probe.cursor.dev=ip->dev",
        "probe.cursor.inum=ip->inum",
        "probe.cursor.incarnation=ip->vfs_incarnation",
        "probe.cursor.inode_size=ip->size",
        "probe.cursor.bank!=bank",
        "probe.cursor.confirm!=confirm",
        "probe.cursor.dev!=ip->dev",
        "probe.cursor.inum!=ip->inum",
        "probe.cursor.incarnation!=ip->vfs_incarnation",
        "probe.cursor.inode_size!=ip->size",
    ):
        require(token in read, f"resumed inode identity binding lost: {token}")
    require(
        "elseif(probe.cursor.bank!=bank||"
        "probe.cursor.confirm!=confirm||"
        "probe.cursor.dev!=ip->dev||"
        "probe.cursor.inum!=ip->inum||"
        "probe.cursor.incarnation!=ip->vfs_incarnation||"
        "probe.cursor.inode_size!=ip->size)" in read,
        "probe cursor identity mismatch guard is weakened",
    )
    require(
        read.count("(status>=0||status==AGENT_META_BANK_CORRUPT)") >= 2,
        "terminal absent/uncommitted/corrupt results are not cached",
    )
    require(
        read.count("probe.summary[bank].classified=1") >= 4
        and read.count("probe.summary[bank].status=status") >= 2
        and "probe.summary[bank].status=AGENT_META_BANK_UNCOMMITTED" in read,
        "terminal classifications are not retained across bounded turns",
    )
    for token in (
        "probe.summary[bank].status=AGENT_META_BANK_VALID",
        "probe.summary[bank].generation=*generation",
        "probe.summary[bank].payload_hash=*payload_hash",
        "probe.summary[bank].migration=*migration",
    ):
        require(token in read, f"valid terminal summary is incomplete: {token}")
    require_order(
        read,
        (
            "probe.cursor.phase=AGENT_META_PROBE_HEADER",
            "agent_metadata_probe_header_valid(store,ip->size)",
            "probe.cursor.phase=AGENT_META_PROBE_PAYLOAD",
            "probe.cursor.phase=AGENT_META_PROBE_VERIFY_HEADER",
            "(char*)&probe.cursor.verify_header",
            "agent_metadata_probe_validate(store,generation,payload_hash,migration)",
        ),
        "header/payload/final-header probe",
    )
    header_valid = compact(
        function_body(probe, "agent_metadata_probe_header_valid(")
    )
    require_order(
        header_valid,
        ("probe.cursor.store_bytes=store_bytes", "probe.cursor.header=store->header"),
        "captured header",
    )
    validate_bank = compact(function_body(probe, "agent_metadata_probe_validate("))
    require(
        "if(memcmp(&probe.cursor.header,&probe.cursor.verify_header,"
        "sizeof(probe.cursor.header))!=0||"
        "store->header.payload_hash!=agent_meta_format_payload_hash("
        in validate_bank,
        "final-header mismatch no longer independently rejects the bank",
    )
    require_order(
        validate_bank,
        (
            "if(memcmp(&probe.cursor.header,&probe.cursor.verify_header,sizeof(probe.cursor.header))!=0",
            "store->header.payload_hash!=agent_meta_format_payload_hash(",
            "agent_meta_format_records_valid(store)",
            "*generation=store->header.generation",
            "*payload_hash=store->header.payload_hash",
        ),
        "final-header and payload validation",
    )
    catalog_progress = compact(
        function_body(probe, "agent_metadata_probe_catalog_progress(")
    )
    require_order(
        catalog_progress,
        (
            "if(bank<0||bank>=AGENT_META_STORE_BANKS||offset==0)",
            "panic(\"metadatacatalogprogressinvariant\")",
            "probe.progress_sequence++",
            "probe.progress_bank=bank",
            "probe.progress_phase=4",
            "probe.progress_offset=offset",
        ),
        "catalog preparation progress evidence",
    )
    require(
        catalog_progress.count("probe.progress_phase=") == 1,
        "catalog progress phase can be overwritten after publication",
    )

    summary = compact(function_body(probe, "agent_metadata_probe_summary("))
    require(
        "if(!key->resumable&&status<0){agent_metadata_probe_reset();"
        "if(status==AGENT_META_BANK_PROGRESS)status=AGENT_META_BANK_BUSY;}"
        in summary,
        "non-resumable summary can retain a partial or confirmed cache",
    )
    confirm = compact(function_body(probe, "agent_metadata_probe_confirm("))
    require(
        "if(key->resumable&&probe.confirmed_bank==bank" in confirm,
        "non-resumable reload can reuse selected-bank confirmation",
    )
    for token in (
        "probe.summary[bank].status==AGENT_META_BANK_VALID",
        "probe.summary[bank].generation==expected_generation",
        "probe.summary[bank].payload_hash==expected_hash",
        "probe.summary[bank].migration==expected_migration",
    ):
        require(token in confirm, f"confirmed candidate cache is under-bound: {token}")
    require(
        "if(key->resumable)probe.confirmed_bank=bank" in confirm,
        "confirmed-bank cache is not restricted to resumable boot loads",
    )
    require_order(
        confirm,
        (
            "agent_metadata_probe_read(key,bank,1,store,&generation,&payload_hash,&migration,0)",
            "if(generation!=expected_generation||payload_hash!=expected_hash||migration!=expected_migration)",
            "agent_metadata_probe_new_epoch()",
            "returnAGENT_META_BANK_CORRUPT",
        ),
        "selected-bank confirmation mismatch",
    )
    mismatch = confirm[
        confirm.find("if(generation!=expected_generation") :
        confirm.find("if(key->resumable)probe.confirmed_bank=bank")
    ]
    require(
        "AGENT_META_BANK_INTERRUPTED" not in mismatch,
        "confirmed generation/hash/migration mismatch is retryable",
    )

    # Selection is read-only, rejects incomplete authority, compares the two
    # generations in the correct direction, then confirms the chosen bank.
    select = compact(function_body(store, "agent_meta_store_select("))
    require(
        ".resumable=!agent_file_loaded" in select,
        "runtime loaded reload incorrectly enables resumable cached reads",
    )
    require(
        "agent_meta_bank_shadow_install" not in select
        and "agent_meta_bank_shadow_valid" not in select,
        "selector mutates trusted bank shadows",
    )
    gate_end = select.find("if(selected<0)")
    require(gate_end >= 0, "selector lost no-valid-bank gate")
    for status in (
        "AGENT_META_BANK_PROGRESS",
        "AGENT_META_BANK_INTERRUPTED",
        "AGENT_META_BANK_BUSY",
        "AGENT_META_BANK_IO",
    ):
        require(
            0 <= select.find(f"status[bank]=={status}") < gate_end,
            f"selector can decide authority before {status}",
        )
    require(
        "generations[bank]>generations[selected]" in select,
        "selector does not choose the highest generation",
    )
    require(
        "generations[bank]==generations[selected]" in select
        and "hashes[bank]!=hashes[selected]" in select,
        "equal-generation split brain is not corruption",
    )
    no_valid = select[gate_end : select.find("intpeer=")]
    require(
        "if(selected<0)returnAGENT_META_BANK_CORRUPT;" in no_valid,
        "stable no-valid authority does not fail closed",
    )
    require(
        "AGENT_META_BANK_ABSENT" not in no_valid
        and "AGENT_META_BANK_UNCOMMITTED" not in no_valid,
        "stable no-valid authority has a bootstrap state exception",
    )
    require_order(
        select,
        (
            "agent_metadata_probe_confirm(&key,selected,store,generations[selected],hashes[selected],migration[selected])",
            "if(confirmed!=AGENT_META_BANK_VALID)",
            "returnconfirmed<0?confirmed:AGENT_META_BANK_CORRUPT",
            "*candidate_epoch=agent_metadata_probe_epoch()",
        ),
        "selected authority confirmation",
    )

    # Catalog admission works on a mutable copy in the inactive shadow. The
    # raw, twice-read store buffer remains immutable until final publication.
    workspace = compact(store[store.find("static union {") : store.find(
        "#define agent_meta_sort_record"
    )])
    require(
        "struct{structagent_metadata_apply_resultresult;intscratch_bank;}load;"
        in workspace,
        "store has no persistent boot apply workspace",
    )
    prepare_store = compact(function_body(store, "agent_meta_store_apply_prepare("))
    require(
        "conststructagent_meta_store*store" in compact(
            store[store.find("static int agent_meta_store_apply_prepare") :
                  store.find("{", store.find("static int agent_meta_store_apply_prepare"))]
        ),
        "verified raw store is mutable during catalog preparation",
    )
    for token in (
        "apply->plan_candidate_epoch!=candidate_epoch",
        "apply->plan_count!=store->header.count",
        "apply->plan_records!=agent_meta_bank_shadow[bank].records",
        "bank=agent_meta_store_active_bank==0?1:0",
        "if(agent_meta_store_active_bank<0)bank=selected_bank==0?1:0",
        "if(bank==agent_meta_store_active_bank)panic(\"metadataapplyscratchaliasesauthority\")",
        "agent_meta_bank_shadow_invalidate(bank)",
        "memmove(agent_meta_bank_shadow[bank].records,store->records,store->header.count*sizeof(store->records[0]))",
        "*plan=agent_meta_bank_shadow[bank].records",
        "agent_metadata_catalog_prepare_snapshot(*plan,store->header.count,reload_one_scope,reload_scope,candidate_epoch,apply)",
    ):
        require(token in prepare_store, f"immutable apply-plan staging lost: {token}")
    require(
        "memmove(store->records" not in prepare_store
        and "((structagent_meta_store*)store)->records" not in prepare_store,
        "catalog plan is written back into the raw verified bank image",
    )
    require(
        prepare_store.count("store->records") == 2,
        "catalog preparation performs an unreviewed raw-record access",
    )
    require(
        prepare_store.count("memmove(") == 1,
        "catalog preparation performs an extra or aliased memory write",
    )
    require(
        len(re.findall(r"(?<![A-Za-z0-9_])bank=(?!=)", prepare_store)) == 2,
        "inactive scratch bank is reassigned after its authority checks",
    )

    catalog_result = compact(
        declaration_body(catalog_h, "struct agent_metadata_apply_result")
    )
    catalog_key = compact(
        declaration_body(catalog_h, "struct agent_catalog_plan_key")
    )
    compact_agent_h = compact(agent_h)
    for token in (
        "#defineAGENT_FILE_META_MAX512",
        "#defineAGENT_FILE_SYSTEM_LIMIT64",
        "#defineAGENT_FILE_SCOPE_LIMIT112",
        "#defineAGENT_FILE_ORDINARY_LIMIT\\(AGENT_FILE_META_MAX-AGENT_FILE_SYSTEM_LIMIT)",
    ):
        require(token in compact_agent_h, f"fixed catalog partition lost: {token}")
    for token in (
        "AGENT_FILE_SCOPE_GUARANTEE",
        "AGENT_FILE_SCOPE_BURST_LIMIT",
    ):
        require(
            token not in agent_h + catalog + vfs,
            f"fixed catalog partition regained an elastic limit: {token}",
        )
    compact_catalog = compact(catalog)
    for token in (
        "AGENT_FILE_SYSTEM_LIMIT+AGENT_FILE_ORDINARY_LIMIT==AGENT_FILE_META_MAX",
        "VFS_SCOPE_MAX_ACTIVE*AGENT_FILE_SCOPE_LIMIT==AGENT_FILE_ORDINARY_LIMIT",
        "AGENT_FILE_SYSTEM_LIMIT:AGENT_FILE_SCOPE_LIMIT",
        "result->owned>=limit",
        "result->ordinary>=AGENT_FILE_ORDINARY_LIMIT",
        "++result->plan_scope_counts[scope_index]>AGENT_FILE_SCOPE_LIMIT",
    ):
        require(token in compact_catalog, f"fixed catalog bound lost: {token}")

    # Same-version history is governed by representation and hard partition
    # bounds. The newer AUTOSCAN reserve is a live growth policy, not a reason
    # to classify an otherwise valid v7 bank as corrupt.
    catalog_plan_count = compact(
        function_body(catalog, "static int agent_catalog_plan_count(")
    )
    for token in (
        "++result->plan_system_count<=AGENT_FILE_SYSTEM_LIMIT",
        "++result->plan_ordinary_count>AGENT_FILE_ORDINARY_LIMIT",
        "++result->plan_scope_counts[scope_index]>AGENT_FILE_SCOPE_LIMIT",
    ):
        require(token in catalog_plan_count,
                f"snapshot hard bound lost: {token}")
    require("AUTOSCAN" not in catalog_plan_count and
            "flags" not in catalog_plan_count,
            "snapshot validation depends on the current autoscan policy")

    hard_admission = compact(function_body(
        catalog, "static int agent_catalog_hard_admission(\n\tuint scope_id"
    ))
    require("AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" not in hard_admission,
            "rollback hard admission depends on the autoscan reserve")
    live_admission = compact(function_body(
        catalog, "static int agent_catalog_admission(\n\tuint scope_id"
    ))
    for token in (
        "agent_catalog_hard_admission(scope_id,except_slot,candidate,&result)",
        "(flags&AGENT_FILE_META_F_AUTOSCAN)",
        "!(old_flags&AGENT_FILE_META_F_AUTOSCAN)",
        "result.autoscan>=AGENT_FILE_AUTOSCAN_SCOPE_LIMIT",
    ):
        require(token in live_admission,
                f"live autoscan delta admission lost: {token}")
    restore = compact(function_body(
        catalog, "int agent_metadata_catalog_restore("
    ))
    require("agent_catalog_hard_admission(" in restore and
            "agent_catalog_admission(" not in restore,
            "receipt rollback is constrained by a newer soft policy")

    # A catalog miss may leave a positive but stale inode sidecar. The scanner
    # must verify that mismatch before using the stale-only converter.
    compact_file_state_h = compact(file_state_h)
    require("intagent_file_state_set_index(structinode*,short,short,int)"
            in compact_file_state_h, "shared sidecar updater is not declared")
    stale_defer = compact(function_body(file_state, "agent_file_state_set_index("))
    require(
        "slot==AGENT_INODE_META_DEFERRED_SLOT&&!stale&&ip->agent_meta_slot>0"
        in stale_defer,
        "verified stale sidecar cannot transition from positive to -1",
    )
    scan_bind = compact(function_body(scan, "agent_metadata_scan_index_inode("))
    require_order(
        scan_bind,
        (
            "if(agent_metadata_catalog_borrow(0,slot,&view)<=0",
            "stale_sidecar=ip->agent_meta_slot>0",
            "agent_metadata_catalog_resolve(scope,&selector,-1,&resolution)",
            "if(slot==AGENT_CATALOG_NO_SPACE)",
            "agent_file_state_set_index(ip,AGENT_INODE_META_DEFERRED_SLOT,0,stale_sidecar)",
        ),
        "verified stale sidecar conversion after catalog miss",
    )
    compact_scope_create = compact(function_body(vfs, "static int vfs_scope_create("))
    require(
        "if(ref->retiring)retiring++" in compact_scope_create
        and "allocated+retiring<VFS_SCOPE_MAX_ACTIVE" in compact_scope_create,
        "retiring scope no longer retains its fixed catalog partition",
    )
    for token in (
        "conststructagent_meta_record*records;",
        "uint64candidate_epoch,catalog_generation,lifecycle_generation;",
        "uintcount,reload_scope;",
        "intreload_one_scope;",
        "uintlifecycle_id;",
    ):
        require(token in catalog_key, f"catalog immutable plan key lost: {token}")
    for token in ("structworkflow_lifecycle_key", "reserved", "topology"):
        require(
            token not in catalog_key,
            f"catalog raw plan key regained lifecycle padding: {token}",
        )
    for token in (
        "intused,layout_changed,prepared,plan_active;",
        "structagent_catalog_plan_keyplan_key;",
        "conststructagent_meta_record*plan_records;",
        "uint64plan_candidate_epoch,plan_catalog_generation,plan_lifecycle_generation;",
        "uint64plan_token,plan_hash;",
        "uintplan_count,plan_reload_scope;",
        "intplan_reload_one_scope;",
        "uintplan_lifecycle_id;",
        "uintplan_cursor,plan_catalog_cursor,plan_next_slot;",
    ):
        require(token in catalog_result, f"catalog plan binding lost: {token}")
    require("plan_scope_autoscan_counts" not in catalog_result,
            "snapshot retains redundant autoscan policy state")
    catalog_prepare = compact(
        function_body(catalog, "agent_metadata_catalog_prepare_snapshot(")
    )
    for token in (
        "result->plan_key=key",
        "structworkflow_lifecycle_keylifecycle=workflow_lifecycle_none()",
        "if(reload_one_scope&&!agent_catalog_scope_admissible(reload_scope,&lifecycle))returnAGENT_METADATA_LOAD_INTERRUPTED",
        "agent_catalog_plan_key(records,count,reload_one_scope,reload_scope,candidate_epoch,agent_catalog_generation,lifecycle)",
        "key.catalog_generation=result->plan_catalog_generation",
        "result->plan_lifecycle_id!=lifecycle.id",
        "result->plan_lifecycle_generation!=lifecycle.generation",
        "memcmp(&result->plan_key,&key,sizeof(key))!=0",
        "result->plan_catalog_generation!=agent_catalog_generation",
        "result->plan_token!=binding",
    ):
        require(token in catalog_prepare, f"catalog continuation is unbound: {token}")
    require_order(
        catalog_prepare,
        (
            "if(record->scope_id!=VFS_SCOPE_SYSTEM&&(",
            "vfs_scope_lifecycle(record->scope_id,&lifecycle)<0",
            "agent_catalog_bit_set(result->missing_slots,original_slot)",
            "gotohash_record",
            "if(agent_catalog_plan_count(result,record->scope_id)<0)",
            "AGENT_METADATA_LOAD_CORRUPT",
        ),
        "cold boot stale lifecycle filtering before capacity admission",
    )
    require("count_status" not in catalog_prepare,
            "partial snapshot overflow migration state remains")
    require("AGENT_FILE_AUTOSCAN_SCOPE_LIMIT" not in catalog_prepare,
            "snapshot prepare treats a live soft limit as corruption")
    classify_missing = compact(function_body(store, "classify_missing("))
    require_order(
        classify_missing,
        (
            "if(reload_one_scope)",
            "if(apply->layout_changed||store_missing(apply))",
            "agent_metadata_store_mark_dirty(reload_scope)",
            "if((apply->missing_slots[record->slot/8]&",
            "agent_metadata_store_mark_dirty(record->scope_id)",
        ),
        "missing snapshot reconciliation",
    )
    require(
        "reload_one_scope?AGENT_FILE_META_MAX:AGENT_CATALOG_PREPARE_STEP"
        in catalog_prepare
        and "reload_one_scope?count:AGENT_CATALOG_PREPARE_STEP"
        in catalog_prepare,
        "foreground one-scope reload can yield a partial catalog plan",
    )
    require(
        catalog_prepare.count("returnAGENT_METADATA_LOAD_PROGRESS;") == 2
        and catalog_prepare.count("AGENT_METADATA_LOAD_PROGRESS") == 2
        and "if(result->plan_catalog_cursor<AGENT_FILE_META_MAX)returnAGENT_METADATA_LOAD_PROGRESS;"
        in catalog_prepare
        and "if(result->plan_cursor<count)returnAGENT_METADATA_LOAD_PROGRESS;"
        in catalog_prepare,
        "catalog progress is not bounded to boot cursor continuation",
    )
    catalog_apply = compact(
        function_body(catalog, "agent_metadata_catalog_apply_snapshot(")
    )
    for token in (
        "!result->plan_active",
        "!result->prepared",
        "structworkflow_lifecycle_keylifecycle=workflow_lifecycle_none()",
        "if(reload_one_scope&&(!agent_catalog_scope_admissible(reload_scope,&lifecycle)",
        "!agent_catalog_scope_admissible(reload_scope,&lifecycle)",
        "result->plan_lifecycle_id!=lifecycle.id",
        "result->plan_lifecycle_generation!=lifecycle.generation",
        "agent_catalog_plan_key(records,count,reload_one_scope,reload_scope,candidate_epoch,agent_catalog_generation,lifecycle)",
        "memcmp(&result->plan_key,&key,sizeof(key))!=0",
        "panic(\"Agentcatalogapplybindinginvariant\")",
        "hash!=result->plan_hash",
        "agent_catalog_plan_final_token(binding,hash)",
        "panic(\"Agentcatalogapplyplaninvariant\")",
    ):
        require(token in catalog_apply, f"catalog apply accepts a stale plan: {token}")

    load = compact(function_body(store, "agent_file_load_snapshot("))
    require(
        "if(agent_file_loaded){memset(&agent_meta_workspace.load,0,sizeof(agent_meta_workspace.load));"
        "agent_meta_workspace.load.scratch_bank=-1;}" in load,
        "runtime reload is not forced into a fresh, single-call plan",
    )
    require_order(
        load,
        (
            "result=agent_meta_store_apply_prepare(",
            "if(result==AGENT_METADATA_LOAD_PROGRESS)",
            "agent_metadata_probe_catalog_progress(selected_bank,apply->plan_catalog_cursor+apply->plan_cursor)",
            "if(result<0)gotoout_store",
            "agent_meta_format_recover_identifiers(store)",
            "result=agent_metadata_catalog_apply_snapshot(",
            "if(result<0)gotoout_store",
            "agent_meta_bank_shadow_install(",
            "agent_meta_store_active_bank=selected_bank",
            "agent_metadata_probe_finish(candidate_epoch)",
            "agent_meta_reconcile_required=1",
            "agent_background_request()",
            "agent_meta_store_io_leave()",
        ),
        "prepare/recover/apply/publication order",
    )
    reconcile_start = load.find("agent_metadata_probe_finish(candidate_epoch)")
    reconcile_end = load.find("agent_meta_store_io_leave()", reconcile_start)
    reconcile_path = load[reconcile_start:reconcile_end]
    require(
        all(gate not in reconcile_path for gate in
            ("if(", "for(", "while(", "switch(")),
        "complete snapshot reconciliation is conditionally gated",
    )
    out_store = load[load.find("out_store:") : load.find("out_txn:")]
    require(
        "if(result!=AGENT_METADATA_LOAD_PROGRESS)agent_meta_store_apply_abort()"
        in out_store,
        "bounded progress discards its persistent apply plan",
    )
    require(
        out_store.count("agent_meta_store_apply_abort()") == 1,
        "out_store contains an unconditional or duplicate apply-plan abort",
    )
    require(
        "agent_metadata_catalog_prepare_abort" not in out_store,
        "progress path can bypass the store-owned apply-plan lifetime",
    )
    require(
        "if(agent_file_loaded||!agent_metadata_recovery_retryable(result))"
        "agent_metadata_probe_reset()" in out_store,
        "boot progress discards its probe candidate",
    )
    require(
        "agent_meta_store_prepare_banks_locked" not in load,
        "load manufactures a bank before authority is known",
    )
    production = store + objects + internal
    for removed in (
        "agent_meta_store_empty_proven",
        "agent_metadata_store_install_empty",
        "agent_file_install_empty_store",
        "agent_metadata_store_has_durable_bank",
    ):
        require(removed not in production, f"runtime bootstrap survived: {removed}")
    build_scope = compact(function_body(store, "agent_meta_store_build_scope("))
    for token in (
        "agent_meta_store_active_bank<0",
        "agent_meta_store_active_bank>=AGENT_META_STORE_BANKS",
        "!agent_meta_bank_shadow_valid[agent_meta_store_active_bank]",
        "base=&agent_meta_bank_shadow[agent_meta_store_active_bank]",
        "store->durable=base->durable",
    ):
        require(token in build_scope, f"store build lacks authority gate: {token}")
    require(
        "agent_durable_arena_init" not in build_scope,
        "store build can mint runtime genesis without authority",
    )
    persist_start = compact(
        function_body(store, "agent_meta_persist_start_locked(uint owner)")
    )
    require_order(
        persist_start,
        (
            "if(!agent_file_loaded||!agent_meta_store_active_verified())",
            "AGENT_METADATA_PERSIST_RECOVERY",
            "if(!agent_meta_store_io_enter())",
        ),
        "persist authority gate",
    )
    meta_init = compact(function_body(objects, "int sys_agent_file_meta_init(void)"))
    require(
        "loaded=agent_file_store_reload(agent_identity_proc_scope(p));"
        "if(loaded<0){result=agent_metadata_load_agent_status(loaded);"
        "gotoout_txn;}" in meta_init,
        "metadata init can bypass a failed durable reload",
    )

    # Progress is a cooperative yield, not a failed retry. Real transient
    # failures retain bounded exponential backoff.
    retryable = compact(
        function_body(recovery, "agent_metadata_recovery_retryable(")
    )
    for token in (
        "status==AGENT_METADATA_LOAD_INTERRUPTED",
        "status==AGENT_METADATA_LOAD_BUSY",
        "status==AGENT_METADATA_LOAD_IO",
        "status==AGENT_METADATA_LOAD_PROGRESS",
    ):
        require(token in retryable, f"retryable result lost: {token}")
    recovery_complete = compact(
        function_body(recovery, "agent_metadata_recovery_complete(")
    )
    progress_start = recovery_complete.find(
        "if(status==AGENT_METADATA_LOAD_PROGRESS)"
    )
    failure_start = recovery_complete.find(
        "if(recovery.failures!=~0U)recovery.failures++"
    )
    require(
        0 <= progress_start < failure_start,
        "progress is charged as a failed retry",
    )
    progress_branch = recovery_complete[progress_start:failure_start]
    require(
        "recovery.retry_tick=now==~0ULL?now:now+1" in progress_branch
        and "recovery.failures" not in progress_branch
        and "observed_failures=" not in progress_branch,
        "progress does not preserve the failure counter",
    )
    require_order(
        recovery_complete[failure_start:],
        (
            "if(recovery.failures!=~0U)recovery.failures++",
            "AGENT_META_BOOT_REPROBE_MAX_SHIFT",
            "delay=1ULL<<shift",
            "if(delay>AGENT_META_BOOT_REPROBE_MAX_TICKS)",
            "delay=AGENT_META_BOOT_REPROBE_MAX_TICKS",
            "now>~0ULL-delay?~0ULL:now+delay",
        ),
        "bounded exponential retry",
    )

    init = compact(function_body(objects, "agent_metadata_storage_init(void)"))
    require_order(
        init,
        (
            "agent_metadata_store_load(&commit)",
            "agent_metadata_recovery_retryable(result)",
            "agent_metadata_store_defer_boot_reprobe(result)",
            "elseif(result<0)",
            "agent_metadata_store_fail_closed_at_boot()",
        ),
        "boot transient/permanent classification",
    )
    admission = compact(
        function_body(objects, "agent_metadata_admission_status(void)")
    )
    durable = compact(
        function_body(objects, "agent_metadata_durable_status(void)")
    )
    require(
        "intagent_metadata_durable_status(void);" in compact(core_internal),
        "durable metadata readiness is not an explicit subsystem contract",
    )
    require(
        "agent_metadata_store_available()&&agent_metadata_store_loaded()"
        in durable
        and "agent_metadata_recovery_pending()?AGENT_STATUS_RETRY:AGENT_STATUS_IO_ERROR"
        in durable
        and "agent_metadata_catalog_reconcile_pending()" not in durable,
        "durable metadata readiness is coupled to catalog projection state",
    )
    require_order(
        admission,
        (
            "status=agent_metadata_durable_status()",
            "if(status!=AGENT_STATUS_OK)returnstatus",
            "agent_metadata_catalog_reconcile_pending()?AGENT_STATUS_RETRY:AGENT_STATUS_OK",
        ),
        "durable-before-catalog admission gate",
    )
    defer = compact(
        function_body(store, "agent_metadata_store_defer_boot_reprobe(")
    )
    for token in (
        "agent_meta_store_failed_closed=1",
        "agent_metadata_recovery_defer(status,agent_ticks())",
        "agent_background_request()",
    ):
        require(token in defer, f"deferred boot fail-closed gate lost: {token}")
    boot_reprobe = compact(
        function_body(objects, "agent_file_store_boot_reprobe(void)")
    )
    require_order(
        boot_reprobe,
        (
            "agent_metadata_recovery_due(agent_file_state_now())",
            "bio_background_begin(FS_OWNER_SYSTEM)",
            "agent_metadata_txn_try_external()",
            "agent_file_store_reload(VFS_SCOPE_NONE)",
            "agent_metadata_store_boot_reprobe_complete(result)",
        ),
        "background fail-closed reprobe",
    )
    require(
        "agent_file_install_empty_store" not in boot_reprobe,
        "background uncertainty can install an empty store",
    )
    core_admission = compact(function_body(core, "agent_core_admission_status(void)"))
    require(
        "agent_metadata_admission_status()" in core_admission
        and "agent_identity_lease_admission_ready()" in core_admission
        and "returnAGENT_STATUS_RETRY" in core_admission,
        "core creation can bypass metadata/identity admission",
    )
    core_storage = compact(function_body(core, "agent_core_storage_init(void)"))
    require_order(
        core_storage,
        (
            "agent_metadata_storage_init()",
            "status=agent_metadata_durable_status()",
            "if(status!=AGENT_STATUS_OK)",
            "agent_obsstore_storage_ready()",
        ),
        "durable identity boot readiness",
    )
    require(
        "agent_metadata_admission_status()" not in core_storage
        and "durableidentityleasedeferreduntilmetadatarecovery" not in core_storage,
        "catalog projection state can still suppress durable identity boot",
    )
    background = compact(function_body(core, "agent_background_maintain(void)"))
    require_order(
        background,
        (
            "agent_metadata_background_maintain()",
            "agent_metadata_durable_status()==AGENT_STATUS_OK",
            "!agent_identity_lease_admission_ready()",
            "agent_obsstore_storage_ready()",
        ),
        "durable identity recovery readiness",
    )
    require(
        "agent_metadata_admission_status()==AGENT_STATUS_OK" not in background,
        "catalog reconciliation can suppress recovered identity leases",
    )
    for signature, operation in (
        ("int sys_agent_create(void)", "agent_create_proc()"),
        ("int sys_agent_create_role(int role)", "agent_create_role_proc(role)"),
        ("int sys_agent_workflow_create(int role)", "agent_workflow_create_proc(role)"),
        ("int sys_agent_worker_create(uint64 pathaddr", "agent_worker_create_proc(path,requested_caps)"),
    ):
        body = compact(function_body(core, signature))
        require_order(
            body,
            ("agent_core_create_admission_status()", operation),
            f"{signature} fail-closed admission",
        )
        if signature != "int sys_agent_worker_create(uint64 pathaddr":
            require(
                f"if(result==AGENT_STATUS_OK)result={operation}" in body,
                f"{signature} does not condition creation on successful admission",
            )
        else:
            require(
                "intadmission=agent_core_create_admission_status();"
                "if(admission!=AGENT_STATUS_OK){"
                "proc_discard_fd_delegations();returnadmission;}" in body,
                "worker creation does not return the failed admission result",
            )

    # Test fault state lives only in excluded profile owners. Production code
    # sees typed, stateless hooks that compile to local no-ops.
    make_compact = compact(makefile)
    require(
        "ifeq($(strip$(AGENT_METADATA_BOOT_READ_FAULT)"
        "$(AGENT_METADATA_SELECT_FAULT_BANK)),)"
        "C_SRCS:=$(filter-out$K/agent_metadata_recovery_test.c,$(C_SRCS))"
        "INACTIVE_PROFILE_C_SRCS+=$K/agent_metadata_recovery_test.cendif"
        in make_compact,
        "production build does not isolate the metadata read-fault owner object",
    )
    require(
        "ifeq($(strip$(AGENT_METADATA_CRASH_PHASE)"
        "$(AGENT_METADATA_EIO_PHASE)),)"
        "C_SRCS:=$(filter-out$K/agent_metadata_test.c,$(C_SRCS))"
        "INACTIVE_PROFILE_C_SRCS+=$K/agent_metadata_test.cendif"
        in make_compact,
        "production build does not isolate the metadata persistence-fault owner object",
    )
    probe_fault = compact(function_body(probe, "agent_metadata_probe_fault("))
    require(
        probe_fault == "returnagent_metadata_recovery_test_fault(bank,allowed);",
        "probe still owns profile fault policy",
    )
    require(
        "#else\nstatic inline void agent_metadata_recovery_test_init" in profile_h
        and "static inline int\nagent_metadata_recovery_test_fault" in profile_h
        and "static inline void agent_metadata_recovery_test_admission" in profile_h,
        "production profile header has no local no-op implementation",
    )
    for token in (
        "AGENT_METADATA_SELECT_FAULT_BANK",
        "AGENT_METADATA_SELECT_FAULT_COUNT",
        "select_remaining",
    ):
        require(token not in probe, f"probe retains select-fault state: {token}")
        require(token in profile, f"read-fault owner lost select state: {token}")
    require("remaining[bank]" in profile, "boot fault injection is not per-bank")
    require(
        "AGENT_METADATA_BOOT_READ_FAULT_BANK >= 0" in profile
        and "bank != AGENT_METADATA_BOOT_READ_FAULT_BANK" in profile,
        "fault profile cannot isolate the potentially newer bank",
    )
    for token in (
        "AGENT_METADATA_EIO_PHASE",
        "AGENT_METADATA_EIO_BANK",
        "AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS",
        "eio_armed",
        "completed_scope_commits",
    ):
        require(token not in store, f"store retains EIO profile state: {token}")
        require(token in persist_profile, f"persistence profile lost EIO state: {token}")
    for hook in (
        "agent_metadata_test_eio_start",
        "agent_metadata_test_eio_cancel",
        "agent_metadata_test_eio_pre_io",
        "agent_metadata_test_eio_commit",
    ):
        require(hook in persist_profile_h, f"persistence profile header lost {hook}")
        require(hook in persist_profile, f"persistence profile owner lost {hook}")
    start = compact(function_body(store, "agent_meta_persist_start_locked("))
    require_order(
        start,
        (
            "agent_meta_persist_target_locked(target_bank,0)",
            "agent_metadata_test_bind(scope_id,captured_generation,agent_meta_persist.job_id)",
            "agent_metadata_test_eio_start(scope_id,agent_meta_persist.job_id)",
        ),
        "profile target binding",
    )
    eio_checkpoint = compact(
        function_body(store, "agent_meta_eio_checkpoint(uint phase)")
    )
    for token in (
        "agent_meta_persist.scope_id",
        "agent_meta_persist.job_id",
        "agent_meta_persist.mirroring",
        "phase",
    ):
        require(token in eio_checkpoint, f"EIO hook is not job-bound: {token}")
    persist_step = compact(function_body(store, "agent_meta_persist_step_locked("))
    for phase in (3, 5, 8):
        require(
            f"agent_meta_eio_checkpoint({phase})" in persist_step,
            f"EIO phase {phase} has no pre-I/O checkpoint",
        )
    for phase_name in (
        "INVALIDATE",
        "PUBLISH",
        "FLUSH_INVALID",
        "FLUSH_PAYLOAD",
        "FLUSH_HEADER",
    ):
        require(
            f"caseAGENT_META_PERSIST_{phase_name}:" in persist_step,
            f"shared EIO phase lost {phase_name}",
        )
    require(
        persist_step.count("uintcheckpoint=agent_meta_persist.phase;") == 2
        and persist_step.count("agent_meta_eio_checkpoint(checkpoint);") == 2,
        "shared header/flush EIO phases are not bound to the current phase",
    )
    compact_store = compact(store)
    phase_names = (
        "INVALIDATE",
        "FLUSH_INVALID",
        "WRITE",
        "FLUSH_PAYLOAD",
        "VERIFY_PAYLOAD",
        "PUBLISH",
        "FLUSH_HEADER",
        "VERIFY_HEADER",
    )
    for phase, phase_name in enumerate(phase_names, 1):
        require(
            f"#defineAGENT_META_PERSIST_{phase_name}{phase}U" in compact_store,
            f"EIO phase identity drifted for {phase_name}",
        )
    unexpected_test_owners = [
        name.removeprefix("os_source:")
        for name, source in sources.items()
        if (name.startswith("os_source:")
            or name in {"file_state", "scan"})
        and "agent_metadata_recovery_test" in source
    ]
    require(
        not unexpected_test_owners,
        "production source gained a direct boot-fault owner dependency: "
        + ", ".join(unexpected_test_owners),
    )
    for variable in (
        "AGENT_METADATA_BOOT_READ_FAULT",
        "AGENT_METADATA_BOOT_READ_FAULT_COUNT",
        "AGENT_METADATA_BOOT_READ_FAULT_BANK",
    ):
        require(
            f"\t{variable}= \\" in makefile,
            f"canonical budget build does not clear {variable}",
        )
    for variable in (
        "AGENT_METADATA_CRASH_PHASE",
        "AGENT_METADATA_CRASH_BANK",
        "AGENT_METADATA_EIO_PHASE",
        "AGENT_METADATA_EIO_BANK",
        "AGENT_METADATA_EIO_SKIP_SCOPE_COMMITS",
        "AGENT_METADATA_SELECT_FAULT_BANK",
        "AGENT_METADATA_SELECT_FAULT_COUNT",
        "AGENT_METADATA_BOOT_READ_FAULT",
        "AGENT_METADATA_BOOT_READ_FAULT_COUNT",
        "AGENT_METADATA_BOOT_READ_FAULT_BANK",
    ):
        require(
            f"'{variable}=$({variable})'" in makefile,
            f"kernel build fingerprint omits {variable}",
        )

    # Dynamic evidence covers all transient causes, both-bank and newer-bank
    # authority, an over-burst foreground reload, and three cached peer states.
    require("agentmetatransient_ucore" in user_make, "transient program not built")
    require("agentmetalarge_ucore" in user_make, "large-bank program not built")
    for token in (
        "unavailable_seen = 1",
        "pid == AGENT_STATUS_RETRY || pid == AGENT_STATUS_IO_ERROR",
        "unavailable_seen=1 recovered=1",
    ):
        require(token in program, f"dynamic fail-closed assertion lost: {token}")
    large = compact(large_program)
    require_order(
        large,
        (
            "#defineLARGE_RECORDS32",
            "if(info.metadata_writeback_dirty!=0",
            "info.metadata_writeback_pending==0",
            "agent_file_meta_init()==AGENT_STATUS_OK",
            "agentmetalarge_ucore:runtime_reload_once=1",
            "agentmetalarge_ucore:seed_ready=1records=%d",
        ),
        "foreground over-burst reload oracle",
    )
    runner_compact = compact(runner)
    for token in (
        "forfault_kindinbusyiointerrupted;do",
        "forfault_targetinallnewer;do",
        "AGENT_METADATA_BOOT_READ_FAULT_COUNT=3",
        "validate-metadata-reprobe-log.py",
        "recovered_generation<=authority_generation",
        "metadata_authority_check:",
    ):
        require(token in runner_compact, f"dynamic reprobe matrix lost: {token}")
    for token in (
        "len(bank[\"store_image\"])<=16*ucore_fs.BSIZE",
        "states!=[expected,\"valid\"]",
        "validate_large_terminal\"${large_image}\"valid",
        "agentmetalarge_ucore:runtime_reload_once=1",
        "forterminalinabsentuncommittedcorrupt;do",
        "mutate_large_peer\"${boot_image}\"\"${terminal}\"",
        "--require-progress",
    ):
        require(token in runner_compact, f"large terminal-peer oracle lost: {token}")
    require(
        "agentmeta_boot_fault:" in runner
        and "require_crash_hook_absent" in runner
        and "metadata boot fault marker leaked into the production kernel" in runner,
        "production-kernel fault-hook attestation lost",
    )


def replace_once(
    sources: dict[str, str], name: str, old: str, new: str
) -> None:
    require(sources[name].count(old) == 1, f"mutation anchor is not unique: {old}")
    sources[name] = sources[name].replace(old, new, 1)


def reorder_apply_before_recovery(sources: dict[str, str]) -> None:
    source = sources["store"]
    call = (
        "\tresult = agent_metadata_catalog_apply_snapshot(\n"
        "\t\tapply_plan, store->header.count, reload_one_scope,\n"
        "\t\treload_scope, candidate_epoch, apply);\n"
    )
    marker = "\t/* Identifier recovery publishes monotonic floors only on first load. */\n"
    require(source.count(call) == 1 and source.count(marker) == 1,
            "reorder mutation anchor is not unique")
    source = source.replace(call, "", 1)
    sources["store"] = source.replace(marker, call + marker, 1)


Mutation = tuple[str, Callable[[dict[str, str]], None]]


def replacement(
    label: str, name: str, old: str, new: str
) -> Mutation:
    return label, lambda sources: replace_once(sources, name, old, new)


def main() -> int:
    original = {
        name: path.read_text(encoding="utf-8") for name, path in FILES.items()
    }
    validate(original)
    mutations: tuple[Mutation, ...] = (
        replacement(
            "map retryable metadata load to I/O error",
            "objects",
            "\t       load_status == AGENT_METADATA_LOAD_PROGRESS ?\n"
            "\t\t       AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;",
            "\t       load_status == AGENT_METADATA_LOAD_PROGRESS ?\n"
            "\t\t       AGENT_STATUS_IO_ERROR : AGENT_STATUS_IO_ERROR;",
        ),
        replacement(
            "map terminal metadata load to retry",
            "objects",
            "\treturn load_status == AGENT_METADATA_LOAD_INTERRUPTED ||\n"
            "\t       load_status == AGENT_METADATA_LOAD_BUSY ||\n"
            "\t       load_status == AGENT_METADATA_LOAD_PROGRESS ?\n"
            "\t\t       AGENT_STATUS_RETRY : AGENT_STATUS_IO_ERROR;",
            "\treturn load_status == AGENT_METADATA_LOAD_INTERRUPTED ||\n"
            "\t       load_status == AGENT_METADATA_LOAD_BUSY ||\n"
            "\t       load_status == AGENT_METADATA_LOAD_PROGRESS ?\n"
            "\t\t       AGENT_STATUS_RETRY : AGENT_STATUS_RETRY;",
        ),
        replacement(
            "collapse scoped reload failure into NO_SPACE",
            "objects",
            "\t\tresult = agent_metadata_load_agent_status(loaded);",
            "\t\tresult = AGENT_STATUS_NO_SPACE;",
        ),
        replacement(
            "collapse metadata set pre-load failure into NO_SPACE",
            "objects",
            "\t\tresult = agent_metadata_load_agent_status(commit_status);",
            "\t\tresult = AGENT_STATUS_NO_SPACE;",
        ),
        replacement(
            "collapse metadata query load failure into NO_SPACE",
            "objects",
            "\t\tresult = agent_metadata_load_agent_status(result);",
            "\t\tresult = AGENT_STATUS_NO_SPACE;",
        ),
        replacement(
            "collapse FILE_META_INIT load failure into NO_SPACE",
            "objects",
            "\t\t\t\tres, agent_metadata_load_agent_status(\n"
            "\t\t\t\t\t     (int)(long)res->value0),",
            "\t\t\t\tres, AGENT_STATUS_NO_SPACE,",
        ),
        replacement(
            "scan stale catalog after summary load failure",
            "objects",
            "\tif (result < 0)\n"
            "\t\treturn agent_metadata_load_agent_status(result);\n"
            "\tfor (int i = 0; i < AGENT_FILE_META_MAX; i++) {",
            "\tif (result < 0)\n"
            "\t\tresult = AGENT_STATUS_NOT_FOUND;\n"
            "\tfor (int i = 0; i < AGENT_FILE_META_MAX; i++) {",
        ),
        replacement(
            "scan stale catalog after structured digest load failure",
            "objects",
            "\t\tif (found < 0)\n"
            "\t\t\treturn agent_metadata_load_agent_status(found);\n"
            "\t\tfor (int i = 0; i < AGENT_FILE_META_MAX; i++) {",
            "\t\tif (found < 0)\n"
            "\t\t\tfound = AGENT_STATUS_NOT_FOUND;\n"
            "\t\tfor (int i = 0; i < AGENT_FILE_META_MAX; i++) {",
        ),
        replacement(
            "fallback plain digest after metadata load failure",
            "objects",
            "\tif (found < 0 && found != AGENT_STATUS_NOT_FOUND)\n"
            "\t\treturn found;",
            "\tif (0 && found < 0 && found != AGENT_STATUS_NOT_FOUND)\n"
            "\t\treturn found;",
        ),
        replacement(
            "collapse summary load failure into not found",
            "objects",
            "\t\t\tagent_result_status(\n"
            "\t\t\t\tres, found, found == AGENT_STATUS_NOT_FOUND ?\n"
            "\t\t\t\t\t\t    \"summary_not_found\" :\n"
            "\t\t\t\t\t\t    \"metadata_unavailable\");",
            "\t\t\tagent_result_status(\n"
            "\t\t\t\tres, AGENT_STATUS_NOT_FOUND, \"summary_not_found\");",
        ),
        replacement(
            "present digest load failure as bad selector",
            "objects",
            "\t\t\t\t rc == AGENT_STATUS_BAD_PARAM ?\n"
            "\t\t\t\t\t \"bad_selector\" : \"metadata_unavailable\");",
            "\t\t\t\t rc == AGENT_STATUS_BAD_PARAM ?\n"
            "\t\t\t\t\t \"bad_selector\" : \"bad_selector\");",
        ),
        replacement(
            "collapse status batch load failure into zero updates",
            "objects",
            "\tload_status = agent_file_store_load();\n"
            "\tif (load_status < 0)\n"
            "\t\treturn agent_metadata_load_agent_status(load_status);",
            "\tload_status = agent_file_store_load();\n"
            "\tif (load_status < 0)\n"
            "\t\treturn 0;",
        ),
        replacement(
            "skip status batch ABI error branch",
            "objects",
            "\tif (updated < 0) {\n"
            "\t\tagent_result_status(res, updated,",
            "\tif (0 && updated < 0) {\n"
            "\t\tagent_result_status(res, updated,",
        ),
        replacement(
            "bypass dependency query load failure",
            "objects",
            "\t\tfound = agent_file_store_load();\n"
            "\t\tif (found < 0) {\n"
            "\t\t\tagent_result_status(res,\n"
            "\t\t\t\tagent_metadata_load_agent_status(found),\n"
            "\t\t\t\t\"metadata_unavailable\");",
            "\t\tfound = agent_file_store_load();\n"
            "\t\tif (0 && found < 0) {\n"
            "\t\t\tagent_result_status(res,\n"
            "\t\t\t\tagent_metadata_load_agent_status(found),\n"
            "\t\t\t\t\"metadata_unavailable\");",
        ),
        replacement(
            "collapse dependency query load failure into NO_SPACE",
            "objects",
            "\t\tfound = agent_file_store_load();\n"
            "\t\tif (found < 0) {\n"
            "\t\t\tagent_result_status(res,\n"
            "\t\t\t\tagent_metadata_load_agent_status(found),\n"
            "\t\t\t\t\"metadata_unavailable\");",
            "\t\tfound = agent_file_store_load();\n"
            "\t\tif (found < 0) {\n"
            "\t\t\tagent_result_status(res, AGENT_STATUS_NO_SPACE,\n"
            "\t\t\t\t\"metadata_unavailable\");",
        ),
        replacement(
            "bypass profile read-fault hook",
            "probe",
            "\treturn agent_metadata_recovery_test_fault(bank, allowed);",
            "\treturn AGENT_META_BANK_VALID;",
        ),
        replacement(
            "drop EIO job binding",
            "store",
            "\tagent_metadata_test_eio_pre_io(\n"
            "\t\tagent_meta_persist.scope_id, agent_meta_persist.job_id,\n"
            "\t\tagent_meta_persist.mirroring, phase);",
            "\tagent_metadata_test_eio_pre_io(\n"
            "\t\tVFS_SCOPE_NONE, 0, agent_meta_persist.mirroring, phase);",
        ),
        replacement(
            "drop EIO phase-8 read checkpoint",
            "store",
            "\t\tagent_meta_eio_checkpoint(8);",
            "\t\t(void)verified_header;",
        ),
        replacement(
            "drop shared header EIO checkpoint",
            "store",
            "\t\tagent_meta_eio_checkpoint(checkpoint);\n"
            "\t\tif (checkpoint == 1) {",
            "\t\t(void)checkpoint;\n\t\tif (checkpoint == 1) {",
        ),
        replacement(
            "drop EIO build fingerprint",
            "make",
            "\t\t'AGENT_METADATA_EIO_PHASE=$(AGENT_METADATA_EIO_PHASE)' \\\n",
            "",
        ),
        replacement(
            "delete cursor advance",
            "probe",
            "\t\tprobe.cursor.offset += n;",
            "\t\tprobe.cursor.offset = 0;",
        ),
        replacement(
            "disable terminal cache",
            "probe",
            "\tif (!confirm && probe.summary[bank].classified) {",
            "\tif (0 && !confirm && probe.summary[bank].classified) {",
        ),
        replacement(
            "weaken operation-key equality",
            "probe",
            "\t    probe.key.reload_scope == key->reload_scope &&",
            "\t    probe.key.reload_scope == key->reload_scope ||",
        ),
        replacement(
            "erase typed checkpoint result",
            "probe",
            "\t\tstruct bio_checkpoint_result checkpoint;",
            "\t\tenum bio_checkpoint_state checkpoint;",
        ),
        replacement(
            "relax shared checkpoint stop predicate",
            "bio_h",
            "\treturn result.state != BIO_CHECKPOINT_READY;",
            "\treturn result.state == BIO_CHECKPOINT_INTERRUPTED;",
        ),
        replacement(
            "allow lost metadata I/O ownership",
            "probe",
            "\t\tif (!agent_metadata_reload_is_current() ||\n"
            "\t\t    !agent_meta_store_io_owned())",
            "\t\tif (!agent_metadata_reload_is_current() &&\n"
            "\t\t    !agent_meta_store_io_owned())",
        ),
        replacement(
            "bypass fail-closed checkpoint predicate",
            "probe",
            "\t\tif (bio_checkpoint_should_stop(checkpoint))",
            "\t\tif (checkpoint.state == BIO_CHECKPOINT_DEFERRED)",
        ),
        replacement(
            "collapse deferred progress into busy",
            "probe",
            "\t\t\treturn checkpoint.state == BIO_CHECKPOINT_DEFERRED ?\n"
            "\t\t\t\t       AGENT_META_BANK_PROGRESS :\n"
            "\t\t\t\t       AGENT_META_BANK_INTERRUPTED;",
            "\t\t\treturn checkpoint.state == BIO_CHECKPOINT_DEFERRED ?\n"
            "\t\t\t\t       AGENT_META_BANK_BUSY :\n"
            "\t\t\t\t       AGENT_META_BANK_INTERRUPTED;",
        ),
        replacement(
            "accept unknown checkpoint state",
            "probe",
            "\t\t\treturn checkpoint.state == BIO_CHECKPOINT_DEFERRED ?\n"
            "\t\t\t\t       AGENT_META_BANK_PROGRESS :\n"
            "\t\t\t\t       AGENT_META_BANK_INTERRUPTED;",
            "\t\t\treturn checkpoint.state == BIO_CHECKPOINT_DEFERRED ?\n"
            "\t\t\t\t       AGENT_META_BANK_PROGRESS :\n"
            "\t\t\t\t       AGENT_META_BANK_VALID;",
        ),
        replacement(
            "drop inode-size cursor binding",
            "probe",
            "\t\t   probe.cursor.inode_size != ip->size) {",
            "\t\t   0) {",
        ),
        replacement(
            "weaken cursor identity mismatch",
            "probe",
            "\t\t   probe.cursor.inum != ip->inum ||",
            "\t\t   probe.cursor.inum != ip->inum &&",
        ),
        replacement(
            "drop final header match",
            "probe",
            "\tif (memcmp(&probe.cursor.header, &probe.cursor.verify_header,",
            "\tif (0 && memcmp(&probe.cursor.header, &probe.cursor.verify_header,",
        ),
        replacement(
            "mask final header mismatch",
            "probe",
            "\t\t   sizeof(probe.cursor.header)) != 0 ||",
            "\t\t   sizeof(probe.cursor.header)) != 0 && 0 ||",
        ),
        replacement(
            "make confirmation mismatch retryable",
            "probe",
            "\t\treturn AGENT_META_BANK_CORRUPT;\n\t}\n\tif (key->resumable)\n\t\tprobe.confirmed_bank = bank;",
            "\t\treturn AGENT_META_BANK_INTERRUPTED;\n\t}\n\tif (key->resumable)\n\t\tprobe.confirmed_bank = bank;",
        ),
        replacement(
            "enable nonresumable confirmed cache",
            "probe",
            "\tif (key->resumable && probe.confirmed_bank == bank &&",
            "\tif (probe.confirmed_bank == bank &&",
        ),
        replacement(
            "under-bind confirmed cache",
            "probe",
            "\t    probe.summary[bank].payload_hash == expected_hash &&",
            "\t    1 &&",
        ),
        replacement(
            "make runtime reload resumable",
            "store",
            "\t\t.resumable = !agent_file_loaded,",
            "\t\t.resumable = 1,",
        ),
        replacement(
            "invert selector generation comparison",
            "store",
            "generations[bank] > generations[selected]",
            "generations[bank] < generations[selected]",
        ),
        replacement(
            "write catalog plan into raw bank",
            "store",
            "memmove(agent_meta_bank_shadow[bank].records, store->records,",
            "memmove(((struct agent_meta_store *)store)->records, store->records,",
        ),
        replacement(
            "add aliased raw-bank write",
            "store",
            "\tstruct agent_metadata_apply_result *apply = &agent_meta_workspace.load.result;",
            "\tstruct agent_metadata_apply_result *apply = &agent_meta_workspace.load.result;\n"
            "\tmemmove((void *)store->records, \"X\", 1);",
        ),
        replacement(
            "write raw bank through a local alias",
            "store",
            "\tstruct agent_metadata_apply_result *apply = &agent_meta_workspace.load.result;\n"
            "\tint bank = agent_meta_workspace.load.scratch_bank;",
            "\tstruct agent_metadata_apply_result *apply = &agent_meta_workspace.load.result;\n"
            "\tint bank = agent_meta_workspace.load.scratch_bank;\n"
            "\tconst struct agent_meta_store *raw = store;\n"
            "\tmemmove((void *)raw->records, \"X\", 1);",
        ),
        replacement(
            "reassign scratch after authority check",
            "store",
            "\t\t\tpanic(\"metadata apply scratch aliases authority\");",
            "\t\t\tpanic(\"metadata apply scratch aliases authority\");\n"
            "\t\tbank = selected_bank;",
        ),
        replacement(
            "alias boot scratch with selected bank",
            "store",
            "\t\t\tbank = selected_bank == 0 ? 1 : 0;",
            "\t\t\tbank = selected_bank;",
        ),
        ("reorder recover and apply", reorder_apply_before_recovery),
        replacement(
            "drop candidate epoch binding",
            "store",
            "\t    (apply->plan_candidate_epoch != candidate_epoch ||",
            "\t    (0 ||",
        ),
        replacement(
            "drop catalog lifecycle generation key",
            "catalog_h",
            "\tuint64 candidate_epoch, catalog_generation, lifecycle_generation;",
            "\tuint64 candidate_epoch, catalog_generation;",
        ),
        replacement(
            "replace scalar lifecycle key with padded struct",
            "catalog_h",
            "\tuint lifecycle_id;",
            "\tstruct workflow_lifecycle_key lifecycle;",
        ),
        replacement(
            "under-bind prepared lifecycle generation",
            "catalog",
            "\t    (result->plan_lifecycle_id != lifecycle.id ||\n"
            "\t     result->plan_lifecycle_generation != lifecycle.generation))",
            "\t    (result->plan_lifecycle_id != lifecycle.id ||\n"
            "\t     0))",
        ),
        replacement(
            "bypass apply lifecycle revalidation",
            "catalog",
            "\t    (!agent_catalog_scope_admissible(reload_scope, &lifecycle) ||\n"
            "\t     result->plan_lifecycle_id != lifecycle.id ||",
            "\t    (0 ||\n"
            "\t     result->plan_lifecycle_id != lifecycle.id ||",
        ),
        replacement(
            "expand the fixed workflow partition",
            "agent_h",
            "#define AGENT_FILE_SCOPE_LIMIT    112",
            "#define AGENT_FILE_SCOPE_LIMIT    400",
        ),
        replacement(
            "drop fixed per-scope snapshot bound",
            "catalog",
            "++result->plan_scope_counts[scope_index] > "
            "AGENT_FILE_SCOPE_LIMIT",
            "++result->plan_scope_counts[scope_index] > "
            "AGENT_FILE_ORDINARY_LIMIT",
        ),
        replacement(
            "disable live autoscan delta admission",
            "catalog",
            "\t    !(old_flags & AGENT_FILE_META_F_AUTOSCAN) &&",
            "\t    0 &&",
        ),
        replacement(
            "allow live autoscan overflow",
            "catalog",
            "result.autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT",
            "result.autoscan > AGENT_FILE_AUTOSCAN_SCOPE_LIMIT",
        ),
        replacement(
            "bind snapshot corruption to live autoscan flags",
            "catalog",
            "agent_catalog_plan_count(result, record->scope_id) < 0",
            "agent_catalog_plan_count(result, record->scope_id, "
            "record->meta.flags) < 0",
        ),
        replacement(
            "apply live autoscan policy to receipt rollback",
            "catalog",
            "agent_catalog_hard_admission(\n"
            "\t\t\t    previous_scope, slot, previous, &result)",
            "agent_catalog_admission(\n"
            "\t\t\t    previous_scope, slot, previous, 0, 0, 0)",
        ),
        replacement(
            "admit stale lifecycle records before capacity checks",
            "catalog",
            "vfs_scope_lifecycle(record->scope_id, &lifecycle) < 0",
            "vfs_scope_lifecycle(record->scope_id, &lifecycle) >= 0",
        ),
        replacement(
            "skip scoped missing-record reconciliation",
            "store",
            "\t\tif (apply->layout_changed || store_missing(apply))\n"
            "\t\t\tagent_metadata_store_mark_dirty(reload_scope);",
            "\t\tif (0)\n"
            "\t\t\tagent_metadata_store_mark_dirty(reload_scope);",
        ),
        replacement(
            "skip boot missing-record reconciliation",
            "store",
            "\t\t\tagent_metadata_store_mark_dirty(record->scope_id);",
            "\t\t\t(void)record;",
        ),
        replacement(
            "skip empty complete snapshot reconciliation",
            "store",
            "\tagent_meta_reconcile_required = 1;\n"
            "\tagent_background_request();",
            "\tif (apply->used != 0) {\n"
            "\t\tagent_meta_reconcile_required = 1;\n"
            "\t\tagent_background_request();\n"
            "\t}",
        ),
        replacement(
            "disable verified stale sidecar conversion",
            "file_state",
            "!stale && ip->agent_meta_slot > 0",
            "ip->agent_meta_slot > 0",
        ),
        replacement(
            "forget the stale positive sidecar classification",
            "scan",
            "stale_sidecar = ip->agent_meta_slot > 0;",
            "stale_sidecar = 0;",
        ),
        replacement(
            "route stale positive sidecars through ordinary deferral",
            "scan",
            "AGENT_INODE_META_DEFERRED_SLOT, 0, stale_sidecar",
            "AGENT_INODE_META_DEFERRED_SLOT, 0, 0",
        ),
        replacement(
            "free retiring catalog partition early",
            "vfs",
            "allocated + retiring < VFS_SCOPE_MAX_ACTIVE",
            "allocated < VFS_SCOPE_MAX_ACTIVE",
        ),
        replacement(
            "discard apply plan on progress",
            "store",
            "\tif (result != AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
            "\tif (result == AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
        ),
        replacement(
            "abort apply plan after progress guard",
            "store",
            "\tif (result != AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
            "\tif (result != AGENT_METADATA_LOAD_PROGRESS)\n"
            "\t\tagent_meta_store_apply_abort();\n"
            "\tagent_meta_store_apply_abort();",
        ),
        replacement(
            "bypass store-owned progress retention",
            "store",
            "\tif (result != AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
            "\tif (result != AGENT_METADATA_LOAD_PROGRESS)\n"
            "\t\tagent_meta_store_apply_abort();\n"
            "\tagent_metadata_catalog_prepare_abort(apply);",
        ),
        replacement(
            "drop catalog progress publication",
            "store",
            "\t\tagent_metadata_probe_catalog_progress(selected_bank, apply->plan_catalog_cursor + apply->plan_cursor);",
            "\t\t(void)selected_bank;",
        ),
        replacement(
            "mislabel catalog progress phase",
            "probe",
            "\tprobe.progress_phase = 4;",
            "\tprobe.progress_phase = 3;",
        ),
        replacement(
            "overwrite catalog progress phase",
            "probe",
            "\tprobe.progress_phase = 4;",
            "\tprobe.progress_phase = 4;\n\tprobe.progress_phase = 3;",
        ),
        replacement(
            "allow foreground catalog progress",
            "catalog",
            "(reload_one_scope ? count : AGENT_CATALOG_PREPARE_STEP)",
            "AGENT_CATALOG_PREPARE_STEP",
        ),
        replacement(
            "return indirect foreground progress",
            "catalog",
            "{\n\tuint limit;\n\tstruct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();\n\tstruct agent_catalog_plan_key key;\n\tuint64 binding;\n\n\tagent_catalog_require_txn();",
            "{\n\tuint limit;\n\tstruct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();\n\tstruct agent_catalog_plan_key key;\n\tuint64 binding;\n"
            "\tint forced = AGENT_METADATA_LOAD_PROGRESS;\n\n"
            "\tagent_catalog_require_txn();\n"
            "\tif (reload_one_scope)\n\t\treturn forced;",
        ),
        replacement(
            "charge progress as retry failure",
            "recovery",
            "\tif (status == AGENT_METADATA_LOAD_PROGRESS) {",
            "\tif (status == AGENT_METADATA_LOAD_PROGRESS && recovery.failures++ > 0) {",
        ),
        replacement(
            "increment failure inside progress branch",
            "recovery",
	        "\t\trecovery.retry_tick = now == ~0ULL ? now : now + 1;",
	        "\t\trecovery.failures += 1;\n"
	        "\t\trecovery.retry_tick = now == ~0ULL ? now : now + 1;",
        ),
        replacement(
            "remove production test-owner filter",
            "make",
            "C_SRCS := $(filter-out $K/agent_metadata_recovery_test.c,$(C_SRCS))",
            "C_SRCS := $(C_SRCS)",
        ),
        replacement(
            "drop corrupt terminal-peer case",
            "runner",
            "for terminal in absent uncommitted corrupt; do",
            "for terminal in absent uncommitted; do",
        ),
        replacement(
            "drop foreground reload oracle",
            "large_program",
            "\t\t\tcheck(agent_file_meta_init() == AGENT_STATUS_OK,",
            "\t\t\tcheck(1,",
        ),
        replacement(
            "accept double absent as genesis",
            "store",
            "\tif (selected < 0)\n\t\treturn AGENT_META_BANK_CORRUPT;",
            "\tif (selected < 0) {\n"
            "\t\tif (status[0] == AGENT_META_BANK_ABSENT &&\n"
            "\t\t    status[1] == AGENT_META_BANK_ABSENT)\n"
            "\t\t\treturn AGENT_META_BANK_ABSENT;\n"
            "\t\treturn AGENT_META_BANK_CORRUPT;\n\t}",
        ),
        replacement(
            "remove retry cap",
            "recovery",
            "\tif (delay > AGENT_META_BOOT_REPROBE_MAX_TICKS)",
            "\tif (delay > ~0ULL)",
        ),
        replacement(
            "couple durable readiness to catalog reconciliation",
            "objects",
            "\tif (agent_metadata_store_available() &&\n"
            "\t    agent_metadata_store_loaded())",
            "\tif (!agent_metadata_catalog_reconcile_pending() &&\n"
            "\t    agent_metadata_store_available() &&\n"
            "\t    agent_metadata_store_loaded())",
        ),
        replacement(
            "gate boot identity on full catalog admission",
            "core",
            "\tstatus = agent_metadata_durable_status();",
            "\tstatus = agent_metadata_admission_status();",
        ),
        replacement(
            "gate recovered identity on full catalog admission",
            "core",
            "\tif (agent_metadata_durable_status() == AGENT_STATUS_OK &&",
            "\tif (agent_metadata_admission_status() == AGENT_STATUS_OK &&",
        ),
        replacement(
            "bypass catalog reconciliation admission",
            "objects",
            "\treturn agent_metadata_catalog_reconcile_pending() ?\n"
            "\t\t       AGENT_STATUS_RETRY : AGENT_STATUS_OK;",
            "\treturn AGENT_STATUS_OK;",
        ),
        replacement(
            "accept double uncommitted as genesis",
            "store",
            "\tif (selected < 0)\n\t\treturn AGENT_META_BANK_CORRUPT;",
            "\tif (selected < 0) {\n"
            "\t\tif (status[0] == AGENT_META_BANK_UNCOMMITTED &&\n"
            "\t\t    status[1] == AGENT_META_BANK_UNCOMMITTED)\n"
            "\t\t\treturn AGENT_META_BANK_UNCOMMITTED;\n"
            "\t\treturn AGENT_META_BANK_CORRUPT;\n\t}",
        ),
        replacement(
            "bypass create admission",
            "core",
            "int admission = agent_core_create_admission_status();",
            "int admission = AGENT_STATUS_OK;",
        ),
        replacement(
            "invert create admission gate",
            "core",
            "\tif (result == AGENT_STATUS_OK)\n\t\tresult = agent_create_proc();",
            "\tif (result != AGENT_STATUS_OK)\n\t\tresult = agent_create_proc();",
        ),
    )
    rejected = 0
    for label, mutate in mutations:
        candidate = dict(original)
        mutate(candidate)
        try:
            validate(candidate)
        except ContractError:
            rejected += 1
        else:
            raise ContractError(f"mutation survived: {label}")
    print(
        "metadata boot reprobe contract: "
        f"ok ({rejected}/{len(mutations)} mutations rejected)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
