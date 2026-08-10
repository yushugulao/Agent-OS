#!/usr/bin/env python3
"""Validate the bounded task-work style Agent maintenance edge."""

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
    marker = f"{name}("
    cursor = 0
    while True:
        start = source.find(marker, cursor)
        if start < 0:
            raise ContractError(f"missing function: {name}")
        opening = source.find("{", start + len(marker))
        semicolon = source.find(";", start + len(marker), opening + 1)
        if opening >= 0 and semicolon < 0:
            depth = 0
            for index in range(opening, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[start : index + 1]
            raise ContractError(f"unterminated function: {name}")
        cursor = start + len(marker)


def require(source: str, fragment: str, message: str) -> None:
    if fragment not in source:
        raise ContractError(message)


def reject(source: str, fragment: str, message: str) -> None:
    if fragment in source:
        raise ContractError(message)


def ordered(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def check(root: Path) -> None:
    background = compact(root / "os/agent_background.c")
    core = compact(root / "os/agent_core.c")
    metadata = compact(root / "os/agent_metadata_objects.c")
    syscall = compact(root / "os/syscall.c")
    trap = compact(root / "os/trap.c")
    vfs = compact(root / "os/vfs_security.c")

    request = function(background, "agent_background_request")
    pending = function(background, "agent_background_work_pending")
    take = function(background, "agent_background_take")
    require(request, "__atomic_store_n(&agent_background_pending,1,__ATOMIC_RELEASE)",
            "background publication is not a release-store")
    require(pending, "__atomic_load_n(&agent_background_pending,__ATOMIC_ACQUIRE)",
            "background observation is not an acquire-load")
    require(take, "__atomic_exchange_n(&agent_background_pending,0,__ATOMIC_ACQ_REL)",
            "background edge is not consumed atomically")
    reject(background, "agent_metadata_store", "retired durable store owns background dispatch")
    reject(background, "agent_identity_lease", "retired lease persistence owns background dispatch")

    maintain = function(core, "agent_background_maintain")
    ordered(
        maintain,
        (
            "agent_observe_recording_suppress_begin(p)",
            "fs_deferred_reclaim_maintain()",
            "agent_metadata_background_maintain()",
            "agent_observe_recording_suppress_end(p)",
        ),
        "background maintenance lost suppression or bounded owner order",
    )
    if maintain.count("agent_metadata_background_maintain()") != 1:
        raise ContractError("one checkpoint may run metadata maintenance more than once")

    checkpoint = function(core, "agent_background_checkpoint")
    ordered(
        checkpoint,
        (
            "pending=agent_background_take()",
            "epoch_due=fs_epoch_should_commit()",
            "fs_epoch_request_begin()",
            "if(pending)agent_background_maintain()",
            "if(fs_epoch_should_commit())(void)fs_epoch_commit()",
            "fs_epoch_request_end()",
        ),
        "checkpoint no longer consumes one edge inside one FS epoch request",
    )
    require(
        checkpoint,
        "if(fs_epoch_request_begin()<0){if(pending)agent_background_request();return;}",
        "failed checkpoint can lose a pending maintenance edge",
    )
    if checkpoint.count("agent_background_maintain()") != 1:
        raise ContractError("checkpoint contains an unbounded maintenance loop")

    live = function(metadata, "agent_metadata_background_maintain")
    ordered(
        live,
        (
            "vfs_scope_reap_pending(agent_file_state_now())",
            "agent_metadata_txn_try_external()",
            "agent_live_query_tombstone_drain(8)",
            "agent_live_query_content_drain(8)",
            "agent_metadata_txn_unlock()",
        ),
        "Live-Query maintenance lost its bounded nonblocking drain",
    )
    require(
        live,
        "if(tombstones==AGENT_STATUS_RETRY||content==AGENT_STATUS_RETRY)agent_background_request()",
        "unfinished Live-Query work is not republished",
    )
    for retired in ("agent_metadata_store", "agent_metadata_scan", "agent_durable_section"):
        reject(live, retired, "retired metadata persistence returned to the hot path")

    dispatch = function(syscall, "syscall")
    if dispatch.count("agent_background_checkpoint()") != 3:
        raise ContractError(
            "syscall return must have one joined-cut and two exclusive observer branches"
        )
    require(
        dispatch,
        "if((!operation_denied||direct_guard.active||file_pin_guard.active)&&"
        "(agent_background_work_pending()||id==SYS_sched_yield))"
        "agent_background_checkpoint();",
        "joined workflow operations or pinned file transactions lost their "
        "in-cut task-work checkpoint",
    )
    ordered(
        dispatch,
        (
            "if((!operation_denied||direct_guard.active||file_pin_guard.active)&&"
            "(agent_background_work_pending()||id==SYS_sched_yield))"
            "agent_background_checkpoint();",
            "agent_execution_contract_file_pin_leave(&file_pin_guard)",
            "agent_execution_contract_direct_leave(&direct_guard)",
            "if(operation_entered)workflow_lifecycle_operation_leave(lifecycle)",
        ),
        "file transaction and lifecycle guards no longer cover the joined "
        "background checkpoint",
    )
    require(
        dispatch,
        "workflow_lifecycle_operation_enter(lifecycle)==0",
        "observer maintenance bypasses the workflow cut gate",
    )
    require(
        dispatch,
        "!(id==SYS_agent_run&&trapframe->a2==0&&trapframe->a3==AGENT_RUN_F_FENCE)",
        "a completed workflow fence can run unsealed maintenance",
    )

    usertrap = function(trap, "usertrap")
    ordered(
        usertrap,
        (
            "kernel_work_begin_background()",
            "agent_background_checkpoint()",
            "kernel_work_end_background()",
        ),
        "timer maintenance is not charged as bounded background work",
    )
    if usertrap.count("agent_background_checkpoint()") != 1:
        raise ContractError("timer interrupt can run more than one checkpoint")

    reap = function(vfs, "vfs_scope_reap_pending")
    reject(reap, "while(", "workflow retirement may busy-loop in one checkpoint")
    require(reap, "vfs_scope_retiring_next_locked()", "retirement scans the registry")
    require(reap, "if(request_next)agent_background_request()",
            "unfinished workflow cleanup loses its continuation edge")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"background dispatch fast-path check failed: {error}", file=sys.stderr)
        return 1
    print("background dispatch fast-path check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
