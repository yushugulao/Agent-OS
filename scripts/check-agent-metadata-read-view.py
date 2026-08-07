#!/usr/bin/env python3
"""验证绑定代际的元数据查询读视图。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compact(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def function(source: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\){{", source)
    if match is None:
        raise ValueError(f"missing function {name}")
    brace = source.find("{", match.start())
    depth = 0
    for offset in range(brace, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : offset + 1]
    raise ValueError(f"unterminated function {name}")


def require(source: str, token: str, message: str) -> None:
    if token not in source:
        raise ValueError(message)


def reject(source: str, token: str, message: str) -> None:
    if token in source:
        raise ValueError(message)


def require_scrubbed_returns(source: str, status: str, scrub: str) -> None:
    returns = list(re.finditer(rf"return{re.escape(status)};", source))
    if not returns:
        raise ValueError(f"missing {status} failure exit")
    for match in returns:
        if not source[: match.start()].endswith(scrub):
            raise ValueError("failed read views can expose a partial snapshot")


def check(root: Path) -> None:
    header = compact(root / "os/agent_metadata_catalog.h")
    catalog = compact(root / "os/agent_metadata_catalog.c")
    query = compact(root / "os/agent_metadata_query.c")
    objects = compact(root / "os/agent_metadata_objects.c")

    for token in (
        "structagent_catalog_read_snapshot",
        "uint64generation;",
        "uintscope_id;",
        "structworkflow_lifecycle_keylifecycle;",
        "uint64candidates[AGENT_CATALOG_READ_WORDS];",
    ):
        require(header, token, f"read snapshot missing {token}")

    begin = function(catalog, "agent_metadata_catalog_read_begin")
    copy = function(catalog, "agent_metadata_catalog_read_copy")
    end = function(catalog, "agent_metadata_catalog_read_end")
    for body, label in ((begin, "begin"), (copy, "copy"), (end, "end")):
        reject(body, "agent_metadata_txn_", f"read {label} re-entered metadata gate")
    for token, label in (
        ("vfs_scope_lifecycle(scope_id,&lifecycle)", "trusted lifecycle capture"),
        ("snapshot->generation=agent_catalog_generation", "catalog generation"),
        ("candidates[word]&agent_catalog_ready_bits[word]", "ready candidate snapshot"),
    ):
        require(begin, token, f"read begin lost {label}")
    if begin.count("intr_save()") != 1 or begin.count("intr_restore(enabled)") < 1:
        raise ValueError("candidate bitmap is not captured in one short IRQ section")
    require(begin, "enabled=intr_save();if(vfs_scope_lifecycle(scope_id,&lifecycle)<0)",
            "lifecycle and catalog generation are not captured atomically")

    for token, label in (
        ("snapshot->generation!=agent_catalog_generation", "generation fence"),
        ("agent_catalog_states[slot]!=0", "pending/quarantine filter"),
        ("lifecycle,snapshot->lifecycle", "slot lifecycle fence"),
        ("*meta=agent_catalog_files[slot]", "owned record copy"),
    ):
        require(copy, token, f"read copy lost {label}")
    require(end, "stable=vfs_scope_lifecycle(snapshot->scope_id,&lifecycle)==0",
            "read end does not revalidate lifecycle")
    require(end, "snapshot->generation==agent_catalog_generation",
            "read end does not revalidate generation")
    reject(begin, "agent_catalog_mutation_owner",
           "read view blocks behind a durable mutation fence")
    reject(copy, "agent_catalog_mutation_owner",
           "record copy blocks behind a durable mutation fence")

    execute = function(query, "agent_metadata_query_execute_snapshot")
    for token, label in (
        ("agent_metadata_catalog_read_begin(", "snapshot begin"),
        ("agent_metadata_catalog_read_next(&snapshot", "bitmap cursor"),
        ("agent_metadata_catalog_read_copy(", "record copy"),
        ("agent_metadata_catalog_read_end(&snapshot)", "publish fence"),
        ("kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS)",
         "bounded scheduler checkpoint"),
    ):
        require(execute, token, f"snapshot query lost {label}")
    reject(execute, "agent_metadata_txn_", "snapshot query entered metadata gate")
    reject(execute, "for(intslot=0;slot<AGENT_FILE_META_MAX",
           "snapshot query returned to a full table cursor")
    query_reset = function(query, "agent_query_result_reset")
    require(query_reset, "memset(r,0,sizeof(*r));",
            "snapshot reset does not clear the complete public result")
    reset = execute.find("agent_query_result_reset(r);")
    begin_read = execute.find("agent_metadata_catalog_read_begin(")
    if reset < 0 or begin_read < 0 or reset >= begin_read:
        raise ValueError("each snapshot attempt can inherit a partial result")

    internal = function(objects, "agent_file_query_internal")
    fast = internal.find("agent_metadata_query_execute_snapshot(")
    locked = internal.find("agent_metadata_txn_lock(1)")
    if fast < 0 or locked < 0 or fast >= locked:
        raise ValueError("query does not select the read view before locked recovery")
    require(internal, "AGENT_QUERY_SNAPSHOT_RETRIES",
            "snapshot retry count is not bounded")
    require(internal, "returnAGENT_STATUS_RETRY;",
            "unstable read view does not fail retryably")
    initial_reset = internal.find("agent_file_query_reset(r);")
    snapshot_call = internal.find("agent_metadata_query_execute_snapshot(")
    if initial_reset < 0 or snapshot_call < 0 or initial_reset >= snapshot_call:
        raise ValueError("snapshot query does not begin with an empty result")
    require_scrubbed_returns(
        internal, "AGENT_STATUS_RETRY", "agent_file_query_reset(r);"
    )
    require(
        internal,
        "if(result<0)agent_file_query_reset(r);returnresult;",
        "failed locked recovery can expose a partial result",
    )

    syscall = function(objects, "sys_agent_file_query")
    reject(syscall, "agent_metadata_txn_lock(",
           "query syscall still holds the global metadata gate")
    reject(syscall, "agent_metadata_txn_unlock(",
           "query syscall retained a global gate exit")
    enter = function(objects, "agent_metadata_tool_enter")
    require(enter, "tool_id==AGENT_TOOL_QUERY_FILE",
            "tool protocol does not select the read view")
    require(enter, "returnAGENT_METADATA_TOOL_READ_VIEW;",
            "tool protocol read-view state is missing")
    leave = function(objects, "agent_metadata_tool_exit")
    require(leave, "if(locked==1)agent_metadata_txn_unlock();",
            "read-view state is mistaken for an owned transaction")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as exc:
        print(f"agent metadata read-view check failed: {exc}", file=sys.stderr)
        return 1
    print("agent metadata read-view check: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
