#!/usr/bin/env python3
"""不可变 Context 归档与活动路径视图的变异防护。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "kernel_h": ROOT / "os/agent.h",
    "user_h": ROOT / "user/include/agent.h",
    "context": ROOT / "os/agent_context.c",
    "path": ROOT / "os/agent_context_path.c",
    "path_h": ROOT / "os/agent_context_path.h",
    "proc_h": ROOT / "os/proc.h",
    "user_lib": ROOT / "user/lib/syscall.c",
    "guest": ROOT / "user/src/agentfinal_ucore.c",
    "evaluation": ROOT / "user/src/agenteval_ucore.c",
    "benchmark": ROOT / "user/src/agentbench_ucore.c",
    "runner": ROOT / "scripts/run-agent-tests.sh",
}
SOURCES = {name: path.read_text(encoding="utf-8") for name, path in PATHS.items()}
MARKER = (
    "agentfinal_ucore: context_active_path=1 archive_retained=1 "
    "direct_query=1 fifo_suffix=1"
)
RO_MARKER = (
    "agentfinal_ucore: context_ro_mapping=1 low_agent_fault=-2 "
    "public_unmapped_fault=-2"
)
RO_STORE_ARM = "agentfinal_ucore: context_ro_store_fault_armed=1"
PUBLIC_LOAD_ARM = "agentfinal_ucore: context_public_unmapped_fault_armed=1"


def body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for offset in range(brace, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise ValueError(f"unterminated function: {signature}")


def ordered(source: str, tokens: tuple[str, ...], component: str) -> None:
    positions = [source.index(token) for token in tokens]
    if positions != sorted(positions):
        raise ValueError(f"{component} order changed: {positions}")


def validate(sources: dict[str, str]) -> None:
    for name in ("kernel_h", "user_h"):
        header = sources[name]
        compact = " ".join(header.split())
        for token in (
            "AGENT_CONTEXT_VERSION 9",
            "uint64 active_path_count;",
            "uint64 active_path_oldest_sequence;",
            "uint64 path_parent_sequence;",
            "AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET",
        ):
            if token not in compact:
                raise ValueError(f"{name} missing {token}")
    for token in ("context_active_path_count", "context_active_path_oldest"):
        if token not in sources["proc_h"]:
            raise ValueError(f"proc state missing {token}")

    context = sources["context"]
    path = sources["path"]
    for removed in (
        "agent_context_active_record(", "agent_context_path_note_",
        "agent_context_path_stats", "context_path_stats",
    ):
        if removed in path or removed in sources["path_h"] or removed in context:
            raise ValueError(f"retired Context proof surface returned: {removed}")
    hash_body = body(path, "agent_context_record_hash(")
    if "record->path_parent_sequence" not in hash_body:
        raise ValueError("path parent is not hash-bound")
    append = body(context, "agent_context_append_flags(")
    ordered(
        append,
        (
            "record.path_parent_sequence = p->context_path_visible_head;",
            "record.record_hash = agent_context_record_hash(&record);",
            "agent_context_append_receipt_prepare(p, &record, slot, &receipt)",
            "agent_context_publish_begin(p);",
            "agent_context_append_receipt_commit(p, &record, &receipt)",
            "agent_context_write_header_locked(p) < 0",
            "agent_context_publish_end(p);",
        ),
        "append publication",
    )
    if "agent_context_active_measure(" in append:
        raise ValueError("append returned to a full active-history walk")
    mapping = body(context, "agent_context_map(")
    alias = body(context, "agent_alias_exec_context(")
    permission = "mapped < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W"
    alias_permission = "i < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W"
    if permission not in mapping or alias_permission not in alias:
        raise ValueError("Context authority/cache page permissions changed")
    if "agent_shadow_kva" in context or "agent_shadow_kva" in sources["proc_h"]:
        raise ValueError("retired Context shadow allocation returned")
    receipt = body(context, "agent_context_append_receipt_commit(")
    for token in (
        "active_count = receipt->before.count + 1;",
        "active_count--;",
        "active_oldest = receipt->evicted_successor;",
        "index->summary.head_hash = record->record_hash;",
    ):
        if token not in receipt:
            raise ValueError(f"incremental receipt missing {token}")
    for token in (
        "struct agent_context_path_index",
        "successors[AGENT_CONTEXT_MAX_RECORDS]",
        "Agent context path index must fit sidecar slack",
        "summary->workflow_lifecycle_generation",
        "summary->branch_generation",
    ):
        if token not in context:
            raise ValueError(f"trusted path index missing {token}")
    measure = body(path, "context_active_walk(")
    for token in (
        "seen < p->context_path_capacity",
        "record.path_parent_sequence >= cursor",
        "record.prev_hash == 0",
        "record.record_hash != expected_hash",
        "record.path_parent_sequence < p->context_path_oldest",
        "record.branch_generation > p->context_branch_generation",
        "kernel_work_checkpoint(1)",
    ):
        if token not in measure:
            raise ValueError(f"active measure missing {token}")
    if measure.count("record.prev_hash == 0") < 2:
        raise ValueError("active measure permits a zero hash on a retained edge")
    query = body(context, "sys_context_query(")
    for token in (
        "agent_context_path_summary_capture(p, &query_summary)",
        "cursor = query_summary.oldest_sequence;",
        "records_examined < query_summary.count",
        "records_examined >= query_summary.count",
        "records_examined++;",
        "agent_context_read_record(",
        "record.path_parent_sequence != previous_sequence",
        "record.prev_hash != previous_hash",
        "path_index->successors[",
        "record.record_hash != query_summary.head_hash",
        "agent_context_path_summary_matches(p, &query_summary)",
        "kernel_work_checkpoint(1)",
    ):
        if token not in query:
            raise ValueError(f"active query missing {token}")
    if "agent_context_active_record(" in query:
        raise ValueError("batch query returned to per-index reverse walks")
    if query.count("records_examined++;") != 1:
        raise ValueError("forward query no longer charges one record per step")
    if "while (seq <= p->context_path_latest" in query:
        raise ValueError("query returned to physical archive scanning")
    rollback = body(context, "sys_context_rollback(")
    ordered(
        rollback,
        (
            "agent_context_path_summary_capture(p, &source_summary)",
            "agent_context_active_measure(p, sequence",
            "branch_generation <= source_summary.branch_generation",
            "agent_context_publish_begin(p);",
            "path_index->magic = 0;",
            "agent_context_active_rebuild(",
            "p->context_path_visible_head = sequence;",
            "p->context_active_path_count = active_path_count;",
            "p->context_active_path_oldest = active_path_oldest;",
            "path_index->summary.head_hash = record.record_hash;",
            "path_index->magic = AGENT_CONTEXT_PATH_INDEX_MAGIC;",
            "agent_context_publish_end(p);",
        ),
        "rollback active projection",
    )
    helper = body(sources["user_lib"], "context_mirror_active_query(")
    for token in (
        "header->active_path_count > header->count",
        "header->latest_sequence - header->oldest_sequence + 1",
        "active_slots[(AGENT_CONTEXT_MAX_RECORDS + 63) / 64]",
        "cursor = header->visible_head_sequence;",
        "expected_hash = header->latest_record_hash;",
        "active_slots[word] |= mask;",
        "active_seen == header->active_path_count",
    ):
        if token not in helper:
            raise ValueError(f"mirror helper missing {token}")
    if "context_mirror_active_record(" in sources["user_lib"]:
        raise ValueError("mirror helper returned to per-index reverse walks")
    mirror_record = body(sources["user_lib"], "context_mirror_record(")
    if "record->record_hash != context_mirror_record_hash(record)" not in mirror_record:
        raise ValueError("mirror helper does not recompute record hashes")
    if "record.record_hash != expected_hash" not in helper:
        raise ValueError("mirror helper does not bind the visible head hash")
    if "path_parent_sequence >= sequence" not in sources["user_lib"]:
        raise ValueError("mirror helper does not reject cycles")
    direct = body(sources["user_lib"], "context_direct_active_query(")
    for token in (
        "AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET",
        "__atomic_load_n(publish_sequence, __ATOMIC_ACQUIRE)",
        "before == after && (after & 1) == 0",
        "context_mirror_active_query(",
        "attempt < 8",
    ):
        if token not in direct:
            raise ValueError(f"direct Context helper missing {token}")
    if direct.count("__atomic_load_n(publish_sequence, __ATOMIC_ACQUIRE)") != 2:
        raise ValueError("direct Context helper lost a seqlock read")
    header_snapshot = body(sources["user_lib"], "context_direct_header_snapshot(")
    if header_snapshot.count(
        "__atomic_load_n(publish_sequence, __ATOMIC_ACQUIRE)"
    ) != 2:
        raise ValueError("direct header helper lost a seqlock read")
    publish_begin = body(context, "agent_context_publish_begin(")
    publish_end = body(context, "agent_context_publish_end(")
    if "__ATOMIC_ACQ_REL" not in publish_begin or "__ATOMIC_RELEASE" not in publish_end:
        raise ValueError("Context publication lost release/acquire ordering")
    managed_zero = body(context, "agent_context_managed_zero(")
    if managed_zero.count("agent_context_managed_zero_range(") != 2:
        raise ValueError("Context clear no longer preserves publication sequence")
    for name in ("guest", "runner"):
        for marker in (MARKER, RO_MARKER, RO_STORE_ARM, PUBLIC_LOAD_ARM):
            if marker not in sources[name]:
                raise ValueError(f"{name} missing Context marker: {marker}")
    guest = sources["guest"]
    isolation = body(guest, "check_context_mapping_isolation(")
    ordered(
        isolation,
        (
            "pid = agent_create_role(AGENT_ROLE_SENTINEL);",
            RO_STORE_ARM,
            "*trusted_page = 0;",
            'check(status == -2, "trusted Context page rejects user store");',
            "pid = fork();",
            PUBLIC_LOAD_ARM,
            "(void)*trusted_page;",
            'check(status == -2, "PUBLIC fork has no Context mapping");',
            RO_MARKER,
        ),
        "Context mapping isolation",
    )
    for token in (
        "final_info.agent_role != AGENT_ROLE_SENTINEL",
        "final_info.context_base != AGENT_CONTEXT_BASE",
        "final_info.is_agent",
        "final_info.context_base != 0",
        "final_info.context_size != 0",
        'waitpid(pid, &status) == pid, "wait Context writer fault"',
        'waitpid(pid, &status) == pid, "wait PUBLIC Context probe"',
    ):
        if token not in isolation:
            raise ValueError(f"Context mapping isolation missing {token}")
    run_child = body(guest, "run_agent_child(")
    if "check_context_mapping_isolation();" not in run_child:
        raise ValueError("Agentfinal does not execute Context mapping isolation")
    runner = sources["runner"]
    for token in (
        '--expected-bad-addr-after "${CONTEXT_RO_STORE_FAULT_MARKER}"',
        '--expected-bad-addr-after "${CONTEXT_PUBLIC_FAULT_MARKER}"',
    ):
        if token not in runner:
            raise ValueError(f"runner does not authorize exact Context fault: {token}")
    for token in (
        "context_detail(abandoned_sequence",
        "context_direct_active_query(",
        "context_mirror_active_query(",
        "direct active path rejects a cycle",
        "direct active path rejects content hash tamper",
        "direct active path rejects head hash tamper",
        "FIFO active path converges to retained suffix",
        "for (int round = 0; round < 2; round++)",
        "header->active_path_count == AGENT_CONTEXT_MAX_RECORDS",
        "incremental summary rollback receipt",
        "incremental summary captured successor",
        "incremental summary second successor",
        "final_header.active_path_oldest_sequence == 129",
        "final_header.latest_record_hash == records[1].record_hash",
    ):
        if token not in guest:
            raise ValueError(f"Guest regression missing {token}")
    for name, token in (
        ("evaluation", "context_direct_header_snapshot("),
        ("benchmark", "context_direct_header_snapshot("),
    ):
        if token not in sources[name]:
            raise ValueError(f"{name} bypasses stable direct Context reads")
    if sources["evaluation"].count("context_direct_active_query(") != 4:
        raise ValueError("evaluation bypasses stable direct Context records")


validate(SOURCES)

MUTATIONS = (
    ("kernel_h", "uint64 path_parent_sequence;"),
    ("kernel_h", "AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET"),
    ("path", "hash = context_hash_mix(hash, record->path_parent_sequence);"),
    ("context", "record.path_parent_sequence = p->context_path_visible_head;"),
    ("context", "agent_context_append_receipt_prepare(p, &record, slot, &receipt)"),
    (
        "context",
        "agent_context_publish_begin(p);\n\tagent_context_write_record(p, slot, &record);",
    ),
    ("context", "agent_context_append_receipt_commit(p, &record, &receipt)"),
    ("context", "mapped < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W"),
    ("context", "i < AGENT_CONTEXT_KERNEL_PAGES ? 0 : PTE_W"),
    ("context", "active_count--;"),
    ("context", "active_oldest = receipt->evicted_successor;"),
    ("context", "Agent context path index must fit sidecar slack"),
    ("path", "record.path_parent_sequence >= cursor"),
    ("path", "record.prev_hash == 0"),
    ("path", "record.record_hash != expected_hash"),
    ("path", "record.branch_generation > p->context_branch_generation"),
    ("path", "kernel_work_checkpoint(1)"),
    ("context", "cursor = query_summary.oldest_sequence;"),
    ("context", "records_examined < query_summary.count"),
    ("context", "records_examined >= query_summary.count"),
    ("context", "records_examined++;"),
    ("context", "record.path_parent_sequence != previous_sequence"),
    ("context", "record.prev_hash != previous_hash"),
    ("context", "path_index->successors["),
    ("context", "record.record_hash != query_summary.head_hash"),
    ("context", "agent_context_path_summary_capture(p, &source_summary)"),
    ("context", "branch_generation <= source_summary.branch_generation"),
    ("context", "path_index->magic = 0;"),
    ("context", "agent_context_active_rebuild("),
    ("context", "path_index->summary.head_hash = record.record_hash;"),
    ("context", "path_index->magic = AGENT_CONTEXT_PATH_INDEX_MAGIC;"),
    ("user_lib", "header->active_path_count > header->count"),
    ("user_lib", "path_parent_sequence >= sequence"),
    ("user_lib", "record->record_hash != context_mirror_record_hash(record)"),
    ("user_lib", "record.record_hash != expected_hash"),
    ("user_lib", "active_slots[word] |= mask;"),
    ("user_lib", "__atomic_load_n(publish_sequence, __ATOMIC_ACQUIRE)"),
    ("user_lib", "before == after && (after & 1) == 0"),
    ("guest", "direct active path rejects a cycle"),
    ("guest", "context_direct_active_query("),
    ("evaluation", "context_direct_active_query("),
    ("benchmark", "context_direct_header_snapshot("),
    ("guest", "direct active path rejects content hash tamper"),
    ("guest", "direct active path rejects head hash tamper"),
    ("guest", "FIFO active path converges to retained suffix"),
    ("guest", "incremental summary rollback receipt"),
    ("guest", "incremental summary captured successor"),
    ("guest", "incremental summary second successor"),
    ("guest", "final_header.active_path_oldest_sequence == 129"),
    ("guest", "final_header.latest_record_hash == records[1].record_hash"),
    ("guest", "pid = agent_create_role(AGENT_ROLE_SENTINEL);"),
    ("guest", "*trusted_page = 0;"),
    ("guest", RO_STORE_ARM),
    ("guest", 'check(status == -2, "trusted Context page rejects user store");'),
    (
        "guest",
        'pid = fork();\n\tcheck(pid >= 0, "fork PUBLIC Context probe");',
    ),
    ("guest", "(void)*trusted_page;"),
    ("guest", PUBLIC_LOAD_ARM),
    ("guest", 'check(status == -2, "PUBLIC fork has no Context mapping");'),
    ("guest", "check_context_mapping_isolation();"),
    ("guest", RO_MARKER),
    ("runner", MARKER),
    ("runner", RO_MARKER),
    (
        "runner",
        '--expected-bad-addr-after "${CONTEXT_RO_STORE_FAULT_MARKER}"',
    ),
    (
        "runner",
        '--expected-bad-addr-after "${CONTEXT_PUBLIC_FAULT_MARKER}"',
    ),
)
for name, token in MUTATIONS:
    if token not in SOURCES[name]:
        raise SystemExit(f"mutation anchor drift: {name}: {token}")
    mutant = dict(SOURCES)
    mutant[name] = mutant[name].replace(token, "", 1)
    try:
        validate(mutant)
    except (ValueError, TypeError):
        continue
    raise SystemExit(f"mutation survived: {name}: {token}")

print(f"[context-active-path] wiring and {len(MUTATIONS)} mutations passed")
