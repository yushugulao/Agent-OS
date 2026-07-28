#!/usr/bin/env python3
"""Mutation guards for immutable Context archives and active-path views."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "kernel_h": ROOT / "os/agent.h",
    "user_h": ROOT / "user/include/agent.h",
    "context": ROOT / "os/agent_context.c",
    "path": ROOT / "os/agent_context_path.c",
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
    hash_body = body(path, "agent_context_record_hash(")
    if "record->path_parent_sequence" not in hash_body:
        raise ValueError("path parent is not hash-bound")
    append = body(context, "agent_context_append_flags(")
    ordered(
        append,
        (
            "record.path_parent_sequence = p->context_path_visible_head;",
            "record.record_hash = agent_context_record_hash(&record);",
            "p->context_path_visible_head = latest->sequence;",
            "agent_context_active_measure(",
            "agent_context_write_header_shadow(p);",
        ),
        "append publication",
    )
    measure = body(path, "context_active_walk(")
    for token in (
        "seen < p->context_path_capacity",
        "record.path_parent_sequence >= cursor",
        "record.record_hash != expected_hash",
        "record.path_parent_sequence < p->context_path_oldest",
    ):
        if token not in measure:
            raise ValueError(f"active measure missing {token}")
    query = body(context, "sys_context_query(")
    for token in (
        "p->context_active_path_count",
        "agent_context_active_record(p, index, &record)",
        "kernel_work_checkpoint(1)",
    ):
        if token not in query and token not in path:
            raise ValueError(f"active query missing {token}")
    if "while (seq <= p->context_path_latest" in query:
        raise ValueError("query returned to physical archive scanning")
    rollback = body(context, "sys_context_rollback(")
    ordered(
        rollback,
        (
            "agent_context_active_measure(p, sequence",
            "p->context_path_visible_head = sequence;",
            "p->context_active_path_count = active_path_count;",
            "p->context_active_path_oldest = active_path_oldest;",
        ),
        "rollback active projection",
    )

    helper = body(sources["user_lib"], "context_mirror_active_query(")
    for token in (
        "header->active_path_count > header->count",
        "header->latest_sequence - header->oldest_sequence + 1",
        "context_mirror_active_record(",
        "record.path_parent_sequence != previous.sequence",
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
    ):
        if token not in guest:
            raise ValueError(f"Guest regression missing {token}")


validate(SOURCES)

MUTATIONS = (
    ("kernel_h", "uint64 path_parent_sequence;"),
    ("path", "hash = context_hash_mix(hash, record->path_parent_sequence);"),
    ("context", "record.path_parent_sequence = p->context_path_visible_head;"),
    ("path", "record.path_parent_sequence >= cursor"),
    ("path", "record.record_hash != expected_hash"),
    ("path", "kernel_work_checkpoint(1)"),
    ("context", "p->context_active_path_count = active_path_count;"),
    ("user_lib", "header->active_path_count > header->count"),
    ("user_lib", "path_parent_sequence >= sequence"),
    ("user_lib", "record->record_hash != context_mirror_record_hash(record)"),
    ("user_lib", "record.record_hash != header->latest_record_hash"),
    ("guest", "direct active path rejects a cycle"),
    ("guest", "direct active path rejects content hash tamper"),
    ("guest", "direct active path rejects head hash tamper"),
    ("guest", "FIFO active path converges to retained suffix"),
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
