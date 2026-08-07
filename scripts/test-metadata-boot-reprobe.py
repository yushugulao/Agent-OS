#!/usr/bin/env python3
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


def validate_work_conserving_probe_model() -> None:
    # authority、store epoch、scope、lifecycle id、lifecycle generation、force
    owner = (7, 11, 20, 1, 101, 1)
    peers = [(7, 11, 20 + i, i + 1, 101 + i, 1) for i in range(1, 4)]
    active: tuple[int, int, int, int, int, int] | None = None
    cursor = 0
    terminal_cache = False
    epoch = 0
    calls = {key: 0 for key in peers}
    observed = {key: [0] for key in peers}

    def is_trusted(key: tuple[int, int, int, int, int, int]) -> bool:
        return key[2] in (0, 1)

    def same_base(left: tuple[int, int, int, int, int, int],
                  right: tuple[int, int, int, int, int, int]) -> bool:
        return left[0] == right[0] and left[1] == right[1] and \
            left[5] == right[5]

    def reset() -> None:
        nonlocal active, cursor, terminal_cache, epoch
        active, cursor, terminal_cache, epoch = None, 0, False, 0

    def bind(key: tuple[int, int, int, int, int, int]) -> bool:
        nonlocal active, cursor, terminal_cache, epoch
        if active is None:
            active, epoch = key, 1
            return True
        exact = epoch != 0 and active == key
        base = same_base(active, key)
        if exact:
            return True
        if (epoch != 0 or terminal_cache) and not base:
            reset()
            active, epoch = key, 1
            return True
        if epoch != 0 and is_trusted(active) and not is_trusted(key):
            return False
        if epoch != 0 and active[2] == key[2] and active[3:5] != key[3:5]:
            reset()
            active, epoch = key, 1
            return True
        # 兼容 scope 仅切换逻辑所有者；扫描游标、摘要和外部 store 缓冲区仍共享。
        active, epoch = key, epoch + 1 if epoch != 0 else 1
        return True

    def finish() -> None:
        nonlocal terminal_cache, epoch
        terminal_cache, epoch = True, 0

    # 原所有者只运行一个有界轮次，之后不再调度。
    require(bind(owner), "the initial ordinary probe could not bind")
    cursor += 1
    require(cursor == 1, "the abandoned owner did not publish one PROGRESS")
    completed: set[tuple[int, int, int, int, int, int]] = set()
    for turn in range(1024 * len(peers)):
        key = peers[turn % len(peers)]
        if key in completed:
            continue
        calls[key] += 1
        before = cursor
        require(bind(key), "a compatible ordinary takeover returned BUSY")
        if terminal_cache:
            completed.add(key)
        else:
            cursor += 1
            if cursor >= 753:
                terminal_cache = True
                completed.add(key)
                finish()
        observed[key].append(cursor)
        require(terminal_cache or cursor > before,
                "a compatible takeover returned without useful progress")
        if len(completed) == len(peers):
            break
    require(owner not in completed and active != owner,
            "completion still depends on the abandoned owner")
    require(len(completed) == len(peers),
            "compatible scopes did not finish through shared-cursor handoff")
    require(all(count < 1024 for count in calls.values()),
            "a work-conserving probe exhausted the public retry cap")
    require(all(all(a <= b for a, b in zip(values, values[1:]))
                for values in observed.values()),
            "a compatible scope observed regressing physical progress")

    # 普通调用方不能接管正在使用的可信恢复游标。
    trusted_key = (7, 11, 0, 0, 0, 1)
    reset()
    require(bind(trusted_key), "trusted recovery could not bind an idle probe")
    cursor = 19
    ordinary = peers[0]
    blocked = not bind(ordinary)
    require(blocked and active == trusted_key and cursor == 19,
            "ordinary takeover displaced trusted recovery")

    # 同 scope 生命周期复用和物理权威变更会重置；仅成功完成不会重置。
    old = (7, 11, 20, 1, 101, 1)
    lifecycle_reuse = (7, 11, 20, 1, 102, 1)
    authority_change = (8, 11, 21, 2, 202, 1)
    store_repurpose = (7, 12, 21, 2, 202, 1)
    reset()
    require(bind(old), "old lifecycle could not bind")
    cursor = 37
    require(bind(lifecycle_reuse) and cursor == 0,
            "same-scope lifecycle ABA retained a stale cursor")
    cursor = 41
    terminal_cache, epoch = True, 0
    require(bind(authority_change) and cursor == 0 and not terminal_cache,
            "authority change retained a stale terminal cache")
    cursor = 43
    terminal_cache, epoch = True, 0
    require(bind(store_repurpose) and cursor == 0 and not terminal_cache,
            "store-buffer repurpose retained a stale terminal cache")
    cursor = 753
    finish()
    require(terminal_cache and cursor == 753,
            "successful finish discarded the terminal summary cache")


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

    # 公共加载结果必须区分有界进度、设备/调度失败和已确认损坏。
    for token in (
        "AGENT_METADATA_LOAD_CORRUPT = -1",
        "AGENT_METADATA_LOAD_INTERRUPTED = -2",
        "AGENT_METADATA_LOAD_BUSY = -3",
        "AGENT_METADATA_LOAD_IO = -4",
        "AGENT_METADATA_LOAD_PROGRESS = -5",
    ):
        require(token in internal, f"load status lost: {token}")

    # 内核加载结果只有一套 Agent ABI 映射；NO_SPACE 仅用于事务准入。
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

    # 前台目录查询必须先分类加载失败，不能转查旧内存目录或把缺失解释为成功。
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

    probe_key = compact(declaration_body(probe_h, "struct agent_metadata_probe_key"))
    for token in (
        "uint64authority_cookie;",
        "uint64store_epoch;",
        "uintreload_scope;",
        "uintworkflow_lifecycle_id;",
        "uint64workflow_lifecycle_generation;",
        "intforce;",
    ):
        require(token in probe_key, f"probe continuation key lost: {token}")
    require("resumable" not in probe_key,
            "probe still has an unsafe caller-selected resumability mode")

    # 可恢复 bank 读取归 probe 而非通用 store-I/O 锁所有者，游标与受检字节同寿命。
    require(
        "agent_meta_store_io_read_bank" not in io_h + io,
        "legacy store_io bank reader still owns recovery policy",
    )
    cursor = compact(declaration_body(probe, "struct agent_metadata_probe_cursor"))
    for token in (
        "signedcharactive,bank,confirm;",
        "ucharphase;",
        "uintoffset,store_bytes,journal_block;",
        "uintdev,inum,inode_size;uint64incarnation;",
        "structagent_meta_store_headerverify_header;",
    ):
        require(token in cursor, f"probe cursor binding lost: {token}")

    # 调度检查点用类型化结果避免混同设备/文件系统整数错误；除 READY 外均停止。
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
        "probe.key.store_epoch==key->store_epoch&&"
        "probe.key.reload_scope==key->reload_scope&&"
        "probe.key.workflow_lifecycle_id==key->workflow_lifecycle_id&&"
        "probe.key.workflow_lifecycle_generation=="
        "key->workflow_lifecycle_generation&&"
        "probe.key.force==key->force)"
        "returnAGENT_META_BANK_VALID;" in bind,
        "probe operation-key equality guard is weakened",
    )
    for token in (
        "probe.key.authority_cookie==key->authority_cookie",
        "probe.key.store_epoch==key->store_epoch",
        "probe.key.reload_scope==key->reload_scope",
        "probe.key.workflow_lifecycle_id==key->workflow_lifecycle_id",
        "probe.key.workflow_lifecycle_generation==key->workflow_lifecycle_generation",
        "probe.key.force==key->force",
        "probe.key=*key",
        "agent_metadata_probe_new_epoch(reuse)",
    ):
        require(token in bind, f"probe operation-key binding lost: {token}")
    state = compact(declaration_body(probe, "static struct"))
    require(
        "cache_ready" in state and "wait_generation" not in state and
        "wait_mask" not in state and "wait_cursor" not in state,
        "probe retained a queue that can strand work behind an abandoned owner",
    )
    require(
        "agent_metadata_probe_wait_add(" not in probe and
        "agent_metadata_probe_wait_next(" not in probe,
        "probe retained owner-dependent waiter helpers",
    )
    new_epoch = compact(
        function_body(probe, "agent_metadata_probe_new_epoch("))
    require(
        "if(!reuse){" in new_epoch and
        "memset(probe.summary,0,sizeof(probe.summary))" in new_epoch and
        "memset(&probe.cursor,0,sizeof(probe.cursor))" in new_epoch and
        "probe.confirmed_bank=-1" in new_epoch,
        "compatible epoch handoff does not preserve the physical cursor/cache",
    )
    require(
        new_epoch.find("if(!reuse){") <
        new_epoch.find("memset(&probe.cursor,0,sizeof(probe.cursor))") <
        new_epoch.find("probe.confirmed_bank=-1") <
        new_epoch.find("}", new_epoch.find("probe.confirmed_bank=-1")),
        "cursor reset escaped the non-reuse epoch branch",
    )
    for token in (
        "intbase=probe.key.authority_cookie==key->authority_cookie",
        "probe.key.store_epoch==key->store_epoch",
        "probe.key.force==key->force",
        "!base",
        "agent_metadata_probe_reset()",
    ):
        require(token in bind, f"physical probe base invalidation lost: {token}")
    require(
        "if((probe.epoch!=0||probe.cache_ready)&&!base)"
        "agent_metadata_probe_reset();" in bind,
        "authority or store-buffer epoch change retains physical probe state",
    )
    require_order(
        bind,
        (
            "if(probe.epoch!=0&&!agent_metadata_probe_trusted(&probe.key))",
            ".id=probe.key.workflow_lifecycle_id",
            ".generation=probe.key.workflow_lifecycle_generation",
            "workflow_lifecycle_scope(lifecycle,&scope)<0",
            "scope!=probe.key.reload_scope",
            "agent_metadata_probe_reset()",
            "probe.key.authority_cookie==key->authority_cookie",
        ),
        "stale ordinary owner lifecycle invalidation",
    )
    require_order(
        bind,
        (
            "probe.key.reload_scope==key->reload_scope",
            "probe.key.workflow_lifecycle_id!=key->workflow_lifecycle_id",
            "probe.key.workflow_lifecycle_generation!=key->workflow_lifecycle_generation",
            "agent_metadata_probe_reset()",
            "probe.key=*key",
        ),
        "same-scope lifecycle ABA invalidation",
    )
    require(
        "if(probe.epoch!=0&&"
        "probe.key.reload_scope==key->reload_scope&&"
        "(probe.key.workflow_lifecycle_id!=key->workflow_lifecycle_id||"
        "probe.key.workflow_lifecycle_generation!="
        "key->workflow_lifecycle_generation))"
        "agent_metadata_probe_reset();" in bind,
        "same-scope lifecycle reuse retains stale physical probe state",
    )
    require(
        "if(probe.epoch!=0&&"
        "agent_metadata_probe_trusted(&probe.key)&&"
        "!agent_metadata_probe_trusted(key))"
        "returnAGENT_META_BANK_BUSY;" in bind,
        "an ordinary scope can take over active trusted recovery",
    )
    require(
        bind.count("returnAGENT_META_BANK_BUSY") == 1 and
        "reuse=probe.epoch!=0||probe.cache_ready" in bind and
        "probe.key=*key;agent_metadata_probe_new_epoch(reuse);"
        in bind,
        "compatible ordinary scope takeover is not work-conserving",
    )
    release = compact(function_body(probe, "agent_metadata_probe_release("))
    require(
        "if(!reusable)" in release and
        "agent_metadata_probe_reset()" in release and
        "memset(&probe.cursor,0,sizeof(probe.cursor))" in release and
        "probe.cache_ready=1" in release and "probe.epoch=0" in release,
        "successful finish does not retain only the verified terminal cache",
    )
    require("memset(probe.summary" not in release,
            "successful finish clears reusable terminal summaries")
    finish = compact(function_body(probe, "agent_metadata_probe_finish("))
    require("agent_metadata_probe_release(1)" in finish,
            "successful scope completion discards the shared verified cache")
    invalidate = compact(
        function_body(probe, "agent_metadata_probe_invalidate("))
    for token in (
        "probe.key.authority_cookie==key->authority_cookie",
        "probe.key.store_epoch==key->store_epoch",
        "probe.key.reload_scope==key->reload_scope",
        "probe.key.force==key->force",
        "agent_metadata_probe_release(0)",
    ):
        require(token in invalidate,
                f"targeted continuation invalidation lost: {token}")

    read = compact(function_body(probe, "agent_metadata_probe_read("))
    require(
        "status=agent_metadata_probe_fault(bank,allow_fault&&!confirm);"
        "if(status!=AGENT_META_BANK_VALID){agent_metadata_probe_release(0);"
        "returnstatus;}" in read,
        "faulted probe retains a continuation lease",
    )
    require_order(
        read,
        (
            "status=agent_metadata_probe_bind(key)",
            "if(status!=AGENT_META_BANK_VALID)returnstatus",
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
    require_order(
        read,
        (
            "agent_metadata_probe_open(bank,&ip)",
            "if(probe.cursor.active&&status!=AGENT_META_BANK_BUSY&&status!=AGENT_META_BANK_IO)",
            "if(probe.cursor.active)",
            "agent_metadata_probe_release(0)",
            "returnstatus",
        ),
        "open error cursor invalidation",
    )
    require(
        "if(probe.cursor.active){agent_metadata_probe_release(0);"
        "returnstatus;}" in read,
        "open BUSY/IO retains a stale cursor",
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
            "if(status==AGENT_META_BANK_BUSY&&probe.progress_sequence!=progress_before)",
            "status=AGENT_META_BANK_PROGRESS",
            "if(status!=AGENT_META_BANK_PROGRESS)",
            "agent_metadata_probe_release(0)",
            "returnstatus",
        ),
        "progress-only cursor continuation",
    )
    require(
        "if(status!=AGENT_META_BANK_PROGRESS)"
        "agent_metadata_probe_release(0);" in read,
        "a non-progress read outcome retains the sticky lease",
    )
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
        ("probe.cursor.store_bytes=store_bytes", "returnAGENT_META_BANK_VALID"),
        "validated header size",
    )
    validate_bank = compact(function_body(probe, "agent_metadata_probe_validate("))
    require(
        "if(memcmp(&store->header,&probe.cursor.verify_header,"
        "sizeof(store->header))!=0||"
        "store->header.payload_hash!=agent_meta_format_payload_hash("
        in validate_bank,
        "final-header mismatch no longer independently rejects the bank",
    )
    require_order(
        validate_bank,
        (
            "if(memcmp(&store->header,&probe.cursor.verify_header,sizeof(store->header))!=0",
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
        "returnagent_metadata_probe_read(key,bank,0,store,generation,"
        "payload_hash,migration,allow_fault);" in summary,
        "summary wrapper no longer preserves bounded progress",
    )
    require("resumable" not in summary,
            "summary retained caller-selected continuation policy")
    confirm = compact(function_body(probe, "agent_metadata_probe_confirm("))
    require(
        "if(probe.confirmed_bank==bank" in confirm,
        "bounded confirmation cannot resume",
    )
    for token in (
        "probe.summary[bank].status==AGENT_META_BANK_VALID",
        "probe.summary[bank].generation==expected_generation",
        "probe.summary[bank].payload_hash==expected_hash",
        "probe.summary[bank].migration==expected_migration",
    ):
        require(token in confirm, f"confirmed candidate cache is under-bound: {token}")
    require(
        "probe.confirmed_bank=bank" in confirm,
        "confirmed-bank cache is not retained for the bound operation",
    )
    require_order(
        confirm,
        (
            "agent_metadata_probe_read(key,bank,1,store,&generation,&payload_hash,&migration,0)",
            "if(generation!=expected_generation||payload_hash!=expected_hash||migration!=expected_migration)",
            "agent_metadata_probe_release(0)",
            "returnAGENT_META_BANK_CORRUPT",
        ),
        "selected-bank confirmation mismatch",
    )
    mismatch = confirm[
        confirm.find("if(generation!=expected_generation") :
        confirm.find("probe.confirmed_bank=bank")
    ]
    require(
        "AGENT_META_BANK_INTERRUPTED" not in mismatch,
        "confirmed generation/hash/migration mismatch is retryable",
    )

    # 选择过程只读：拒绝不完整权威，正确比较两代，再确认所选 bank。
    select = compact(function_body(store, "agent_meta_store_select("))
    require(
        "agent_meta_store_probe_key(force,reload_scope,&key)" in select,
        "selector bypasses the stable continuation key",
    )
    probe_key_builder = compact(
        function_body(store, "agent_meta_store_probe_key("))
    for token in (
        "key->authority_cookie=store_authority_cookie()",
        "key->store_epoch=store_buf_epoch",
        "key->reload_scope=reload_scope",
        "key->force=force",
        "if(!force||!agent_file_loaded)returnAGENT_META_BANK_VALID",
        "if(reload_scope==VFS_SCOPE_SYSTEM)returnAGENT_META_BANK_VALID",
        "vfs_scope_lifecycle(reload_scope,&lifecycle)<0",
        "key->workflow_lifecycle_id=lifecycle.id",
        "key->workflow_lifecycle_generation=lifecycle.generation",
    ):
        require(token in probe_key_builder,
                f"runtime reload key is under-bound: {token}")
    require_order(
        probe_key_builder,
        (
            "vfs_scope_lifecycle(reload_scope,&lifecycle)",
            "agent_metadata_probe_invalidate(key)",
            "returnAGENT_META_BANK_INTERRUPTED",
        ),
        "invalid scoped lifecycle discards continuation",
    )
    authority_cookie = compact(function_body(store, "store_authority_cookie("))
    require("agent_meta_format_hash_mix(cookie,store_buf_epoch)" in authority_cookie,
            "shared store buffer reuse is absent from the probe authority key")
    require(
        "if(agent_file_loaded&&agent_metadata_probe_epoch()==0)" in
        compact(function_body(store, "agent_file_load_snapshot(")),
        "runtime reload destroys a live sticky continuation",
    )
    repurpose = compact(
        function_body(store, "agent_meta_store_buffer_repurpose("))
    require("agent_metadata_probe_reset()" in repurpose and
            "store_buf_epoch++" in repurpose and
            "if(store_buf_epoch==0)store_buf_epoch++" in repurpose,
            "store buffer reuse does not revoke an old probe")
    persist_start = compact(
        function_body(store, "agent_meta_persist_start_locked("))
    require_order(
        persist_start,
        (
            "if(!use_journal)",
            "agent_meta_store_buffer_repurpose()",
            "agent_meta_store_build_scope(",
        ),
        "full-COW buffer reuse invalidation",
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
        "if(selected<0||generations[bank]>generations[selected])"
        "selected=bank;elseif(generations[bank]==generations[selected])" in select,
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

    # 目录准入在非活动影子副本上修改；两次读取的原始 store 缓冲区在发布前不变。
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

    # 同版本历史只受表示和硬分区上限约束；新 AUTOSCAN 预留是在线增长策略，
    # 不能据此把原本有效的 v7 bank 判为损坏。
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

    # 目录未命中可能留下正值但过期的 inode sidecar；扫描器须先验证再转换。
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
    for token in (
        "vfs_scope_registry_init_locked()",
        "vfs_scope_find_locked(scope_id)==0",
        "registry->free_count>0",
        "registry->active_count+registry->retiring_count<VFS_SCOPE_MAX_ACTIVE",
        "registry->active_count+registry->retiring_count<VFS_SCOPE_LIFECYCLE_CAP",
    ):
        require(
            token in compact_scope_create,
            f"scope admission lost O(1) registry accounting: {token}",
        )
    require(
        "for(" not in compact_scope_create and "while(" not in compact_scope_create,
        "scope admission regressed to a registry-wide recount",
    )
    retire_add = compact(function_body(vfs, "vfs_scope_retiring_add_locked("))
    require_order(
        retire_add,
        (
            "registry->active_count--",
            "registry->retiring_count++",
            "vfs_scope_registry_check_locked()",
        ),
        "active-to-retiring registry accounting",
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
        "if(agent_file_loaded&&agent_metadata_probe_epoch()==0)" in load,
        "a competing runtime scope discards the sticky reload state",
    )
    require(
        "if(apply->plan_active&&"
        "(!agent_metadata_recovery_retryable(select_status)||"
        "agent_metadata_probe_epoch()==0))agent_meta_store_apply_abort()"
        in load,
        "lease contention discards another scope's catalog progress",
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
        "if(result!=AGENT_METADATA_LOAD_PROGRESS)"
        "agent_metadata_probe_reset()" in out_store,
        "runtime progress discards its sticky probe candidate",
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

    # PROGRESS 是协作让出而非失败重试；真实瞬态失败仍采用有界指数退避。
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
    ):
        require(token in defer, f"deferred boot fail-closed gate lost: {token}")
    require(
        "agent_background_request()" not in defer,
        "boot recovery bypasses its timer deadline",
    )
    metadata_tick = compact(function_body(objects, "agent_metadata_tick("))
    require(
        "if(agent_metadata_recovery_pending()&&"
        "agent_metadata_recovery_due(now))agent_background_request();"
        in metadata_tick,
        "timer no longer publishes due boot recovery work",
    )
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

    # 测试故障状态仅存在于排除的 profile 所有者；生产代码只见无状态类型化空钩子。
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
    ):
        require(token in persist_profile, f"persistence profile lost EIO state: {token}")
    for token in (
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
    require(
        "AGENT_METADATA_EIO_PHASE > 7" in persist_profile
        and "AGENT_METADATA_EIO_PHASE must be in [1, 7]" in persist_profile,
        "EIO profile admits the non-I/O commit checkpoint",
    )
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
    for phase in range(1, 8):
        require(
            f"agent_meta_eio_checkpoint({phase})" in persist_step,
            f"EIO phase {phase} has no pre-I/O checkpoint",
        )
    for phase_name in (
        "INVALIDATE",
        "WRITE",
        "FLUSH_PREPARED",
        "VERIFY_PAYLOAD",
        "PUBLISH",
        "FLUSH_HEADER",
        "VERIFY_HEADER",
    ):
        require(
            f"caseAGENT_META_PERSIST_{phase_name}:" in persist_step,
            f"persistence phase lost {phase_name}",
        )
    require(
        "agent_meta_crash_checkpoint(8)" in persist_step,
        "commit crash checkpoint is not represented",
    )
    compact_store = compact(store)
    phase_names = (
        "INVALIDATE",
        "WRITE",
        "FLUSH_PREPARED",
        "VERIFY_PAYLOAD",
        "PUBLISH",
        "FLUSH_HEADER",
        "VERIFY_HEADER",
        "COMMIT",
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

    # 动态证据覆盖全部瞬态原因、双 bank/新 bank 权威、超 burst 前台重载及三种 peer 缓存态。
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
            "agentmetalarge_ucore:runtime_reload_completed=1",
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
        "agentmetalarge_ucore:runtime_reload_completed=1",
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
    marker = "\t/* 标识符恢复只在首次加载时发布单调水位。 */\n"
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
    validate_work_conserving_probe_model()
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
            "drop EIO phase-7 header-read checkpoint",
            "store",
            "\t\tagent_meta_eio_checkpoint(7);",
            "\t\t(void)verified_header;",
        ),
        replacement(
            "drop prepared-fence EIO checkpoint",
            "store",
            "\t\tagent_meta_eio_checkpoint(3);",
            "\t\t(void)state;",
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
            "\t    probe.key.store_epoch == key->store_epoch &&\n"
            "\t    probe.key.reload_scope == key->reload_scope &&\n"
            "\t    probe.key.workflow_lifecycle_id == key->workflow_lifecycle_id &&",
            "\t    probe.key.store_epoch == key->store_epoch &&\n"
            "\t    probe.key.reload_scope == key->reload_scope ||\n"
            "\t    probe.key.workflow_lifecycle_id == key->workflow_lifecycle_id &&",
        ),
        replacement(
            "drop explicit store epoch continuation binding",
            "probe_h",
            "\tuint64 store_epoch;",
            "\tuint64 ignored_epoch;",
        ),
        replacement(
            "clear the physical cursor on a compatible takeover",
            "probe",
            "\tif (!reuse) {\n"
            "\t\tmemset(probe.summary, 0, sizeof(probe.summary));",
            "\tif (1) {\n"
            "\t\tmemset(probe.summary, 0, sizeof(probe.summary));",
        ),
        replacement(
            "reuse a stale cursor after a physical base change",
            "probe",
            "\tif (!reuse) {\n"
            "\t\tmemset(probe.summary, 0, sizeof(probe.summary));",
            "\tif (0) {\n"
            "\t\tmemset(probe.summary, 0, sizeof(probe.summary));",
        ),
        replacement(
            "retain cache across authority or store epoch change",
            "probe",
            "\tif ((probe.epoch != 0 || probe.cache_ready) && !base)\n"
            "\t\tagent_metadata_probe_reset();",
            "\tif ((probe.epoch != 0 || probe.cache_ready) && 0 && !base)\n"
            "\t\tagent_metadata_probe_reset();",
        ),
        replacement(
            "retain cursor after ordinary owner lifecycle expires",
            "probe",
            "\t\tif (workflow_lifecycle_scope(lifecycle, &scope) < 0 ||\n"
            "\t\t    scope != probe.key.reload_scope)\n"
            "\t\t\tagent_metadata_probe_reset();",
            "\t\tif (workflow_lifecycle_scope(lifecycle, &scope) < 0 ||\n"
            "\t\t    scope != probe.key.reload_scope)\n"
            "\t\t\tprobe.epoch = probe.epoch;",
        ),
        replacement(
            "retain cursor across same-scope lifecycle ABA",
            "probe",
            "\tif (probe.epoch != 0 && probe.key.reload_scope == key->reload_scope &&\n"
            "\t    (probe.key.workflow_lifecycle_id != key->workflow_lifecycle_id ||\n"
            "\t     probe.key.workflow_lifecycle_generation !=\n"
            "\t\t     key->workflow_lifecycle_generation))\n"
            "\t\tagent_metadata_probe_reset();",
            "\tif (0 && probe.epoch != 0 &&\n"
            "\t    probe.key.reload_scope == key->reload_scope)\n"
            "\t\tagent_metadata_probe_reset();",
        ),
        replacement(
            "let an ordinary scope displace trusted recovery",
            "probe",
            "\tif (probe.epoch != 0 && agent_metadata_probe_trusted(&probe.key) &&\n"
            "\t    !agent_metadata_probe_trusted(key))\n"
            "\t\treturn AGENT_META_BANK_BUSY;",
            "\tif (0 && agent_metadata_probe_trusted(&probe.key) &&\n"
            "\t    !agent_metadata_probe_trusted(key))\n"
            "\t\treturn AGENT_META_BANK_BUSY;",
        ),
        replacement(
            "make compatible ordinary takeover wait for its owner",
            "probe",
            "\treuse = probe.epoch != 0 || probe.cache_ready;\n"
            "\tprobe.key = *key;",
            "\tif (probe.epoch != 0)\n"
            "\t\treturn AGENT_META_BANK_BUSY;\n"
            "\treuse = probe.epoch != 0 || probe.cache_ready;\n"
            "\tprobe.key = *key;",
        ),
        replacement(
            "force a compatible successor to reread both banks",
            "probe",
            "\treuse = probe.epoch != 0 || probe.cache_ready;",
            "\treuse = 0;",
        ),
        replacement(
            "discard the verified cache on successful finish",
            "probe",
            "\tagent_metadata_probe_release(1);",
            "\tagent_metadata_probe_reset();",
        ),
        replacement(
            "clear terminal summaries on successful finish",
            "probe",
            "\tmemset(&probe.cursor, 0, sizeof(probe.cursor));\n"
            "\tprobe.cache_ready = 1;",
            "\tmemset(&probe.cursor, 0, sizeof(probe.cursor));\n"
            "\tmemset(probe.summary, 0, sizeof(probe.summary));\n"
            "\tprobe.cache_ready = 1;",
        ),
        replacement(
            "drop reusable terminal cache marker",
            "probe",
            "\tprobe.cache_ready = 1;",
            "\tprobe.cache_ready = 0;",
        ),
        replacement(
            "retain a faulted continuation lease",
            "probe",
            "\t\tif (status != AGENT_META_BANK_VALID) {\n"
            "\t\t\tagent_metadata_probe_release(0);\n"
            "\t\t\treturn status;\n\t\t}",
            "\t\tif (status != AGENT_META_BANK_VALID)\n"
            "\t\t\treturn status;",
        ),
        replacement(
            "globally reset another scope on invalid lifecycle",
            "store",
            "\t\tagent_metadata_probe_invalidate(key);",
            "\t\tagent_metadata_probe_reset();",
        ),
        replacement(
            "erase live runtime continuation before retry",
            "store",
            "\tif (agent_file_loaded && agent_metadata_probe_epoch() == 0) {",
            "\tif (agent_file_loaded) {",
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
            "\tif (memcmp(&store->header, &probe.cursor.verify_header,",
            "\tif (0 && memcmp(&store->header, &probe.cursor.verify_header,",
        ),
        replacement(
            "mask final header mismatch",
            "probe",
            "\t\t   sizeof(store->header)) != 0 ||",
            "\t\t   sizeof(store->header)) != 0 && 0 ||",
        ),
        replacement(
            "make confirmation mismatch retryable",
            "probe",
            "\t\treturn AGENT_META_BANK_CORRUPT;\n\t}\n\tprobe.confirmed_bank = bank;",
            "\t\treturn AGENT_META_BANK_INTERRUPTED;\n\t}\n\tprobe.confirmed_bank = bank;",
        ),
        replacement(
            "bypass confirmed-bank identity",
            "probe",
            "\tif (probe.confirmed_bank == bank &&",
            "\tif (1 &&",
        ),
        replacement(
            "under-bind confirmed cache",
            "probe",
            "\t    probe.summary[bank].payload_hash == expected_hash &&",
            "\t    1 &&",
        ),
        replacement(
            "drop runtime lifecycle generation binding",
            "store",
            "\tkey->workflow_lifecycle_generation = lifecycle.generation;",
            "\tkey->workflow_lifecycle_generation = 0;",
        ),
        replacement(
            "drop runtime lifecycle id binding",
            "store",
            "\tkey->workflow_lifecycle_id = lifecycle.id;",
            "\tkey->workflow_lifecycle_id = 0;",
        ),
        replacement(
            "drop lifecycle key equality",
            "probe",
            "\t    probe.key.workflow_lifecycle_id == key->workflow_lifecycle_id &&",
            "\t    1 &&",
        ),
        replacement(
            "drop lifecycle generation equality",
            "probe",
            "\t    probe.key.workflow_lifecycle_generation ==\n"
            "\t\t    key->workflow_lifecycle_generation &&",
            "\t    1 &&",
        ),
        replacement(
            "drop store buffer authority epoch",
            "store",
            "\tcookie = agent_meta_format_hash_mix(cookie, store_buf_epoch);",
            "\t(void)store_buf_epoch;",
        ),
        replacement(
            "retain probe across store buffer reuse",
            "store",
            "\tagent_metadata_probe_reset();\n\tstore_buf_epoch++;",
            "\tstore_buf_epoch++;",
        ),
        replacement(
            "retain lease after non-progress error",
            "probe",
            "\tif (status != AGENT_META_BANK_PROGRESS)\n"
            "\t\tagent_metadata_probe_release(0);",
            "\tif (status != AGENT_META_BANK_PROGRESS)\n"
            "\t\tprobe.cache_ready = 1;",
        ),
        replacement(
            "retain lease after open error",
            "probe",
            "\t\tif (probe.cursor.active) {\n"
            "\t\t\tagent_metadata_probe_release(0);\n"
            "\t\t\treturn status;\n\t\t}",
            "\t\tif (probe.cursor.active)\n\t\t\treturn status;",
        ),
        replacement(
            "invert selector generation comparison",
            "store",
            "if (selected < 0 || generations[bank] > generations[selected])",
            "if (selected < 0 || generations[bank] < generations[selected])",
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
            "registry->active_count + registry->retiring_count <\n"
            "\t\t    VFS_SCOPE_MAX_ACTIVE",
            "registry->active_count < VFS_SCOPE_MAX_ACTIVE",
        ),
        replacement(
            "lose retiring registry accounting",
            "vfs",
            "\tregistry->retiring_count++;",
            "\tregistry->active_count++;",
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
            "agent_metadata_durable_status(void)\n"
            "{\n"
            "\tif (agent_metadata_store_available() &&\n"
            "\t    agent_metadata_store_loaded())",
            "agent_metadata_durable_status(void)\n"
            "{\n"
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
