#!/usr/bin/env python3
"""Mutation guards for immutable Context archives and active-path views."""

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
    "runner": ROOT / "scripts/run-agent-tests.sh",
}
SOURCES = {name: path.read_text(encoding="utf-8") for name, path in PATHS.items()}
MARKER = (
    "agentfinal_ucore: context_active_path=1 archive_retained=1 "
    "direct_query=1 fifo_suffix=1"
)


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
            "AGENT_CONTEXT_VERSION 8",
            "uint64 active_path_count;",
            "uint64 active_path_oldest_sequence;",
            "uint64 path_parent_sequence;",
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
            "agent_context_append_receipt_commit(p, &record, &receipt)",
            "agent_context_write_header_shadow(p);",
        ),
        "append publication",
    )
    if "agent_context_active_measure(" in append:
        raise ValueError("append returned to a full active-history walk")
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
            "path_index->magic = 0;",
            "agent_context_active_rebuild(",
            "p->context_path_visible_head = sequence;",
            "p->context_active_path_count = active_path_count;",
            "p->context_active_path_oldest = active_path_oldest;",
            "path_index->summary.head_hash = record.record_hash;",
            "path_index->magic = AGENT_CONTEXT_PATH_INDEX_MAGIC;",
        ),
        "rollback active projection",
    )
    helper = body(sources["user_lib"], "context_mirror_active_query(")
    for token in (
        "header->active_path_count > header->count",
        "header->latest_sequence - header->oldest_sequence + 1",
        "context_mirror_active_record(",
        "record.path_parent_sequence != previous.sequence",
        "record.prev_hash != previous.record_hash",
    ):
        if token not in helper:
            raise ValueError(f"mirror helper missing {token}")
    mirror_record = body(sources["user_lib"], "context_mirror_record(")
    if "record->record_hash != context_mirror_record_hash(record)" not in mirror_record:
        raise ValueError("mirror helper does not recompute record hashes")
    if "record.record_hash != header->latest_record_hash" not in helper:
        raise ValueError("mirror helper does not bind the visible head hash")
    if "path_parent_sequence >= sequence" not in sources["user_lib"]:
        raise ValueError("mirror helper does not reject cycles")
    for name in ("guest", "runner"):
        if MARKER not in sources[name]:
            raise ValueError(f"{name} missing active-path marker")
    guest = sources["guest"]
    for token in (
        "context_detail(abandoned_sequence",
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


validate(SOURCES)

MUTATIONS = (
    ("kernel_h", "uint64 path_parent_sequence;"),
    ("path", "hash = context_hash_mix(hash, record->path_parent_sequence);"),
    ("context", "record.path_parent_sequence = p->context_path_visible_head;"),
    ("context", "agent_context_append_receipt_prepare(p, &record, slot, &receipt)"),
    ("context", "agent_context_append_receipt_commit(p, &record, &receipt)"),
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
    ("user_lib", "record.record_hash != header->latest_record_hash"),
    ("user_lib", "record.prev_hash != previous.record_hash"),
    ("guest", "direct active path rejects a cycle"),
    ("guest", "direct active path rejects content hash tamper"),
    ("guest", "direct active path rejects head hash tamper"),
    ("guest", "FIFO active path converges to retained suffix"),
    ("guest", "incremental summary rollback receipt"),
    ("guest", "incremental summary captured successor"),
    ("guest", "incremental summary second successor"),
    ("guest", "final_header.active_path_oldest_sequence == 129"),
    ("guest", "final_header.latest_record_hash == records[1].record_hash"),
    ("runner", MARKER),
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
