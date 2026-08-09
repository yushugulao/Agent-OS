#!/usr/bin/env python3
"""Static contract for member+closing workflow teardown and finalization."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ProtocolError(RuntimeError):
    pass


SOURCE_PATHS = {
    "lifecycle": "os/workflow_lifecycle.c",
    "vfs": "os/vfs_security.c",
    "proc": "os/proc.c",
}


def compact(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def load_sources(root: Path) -> dict[str, str]:
    return {
        name: compact((root / relative).read_text(encoding="utf-8"))
        for name, relative in SOURCE_PATHS.items()
    }


def function(source: str, name: str) -> str:
    marker = f"{name}("
    cursor = 0
    while True:
        start = source.find(marker, cursor)
        if start < 0:
            raise ProtocolError(f"missing function: {name}")
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
            raise ProtocolError(f"unterminated function: {name}")
        cursor = start + len(marker)


def require(source: str, fragment: str, message: str) -> None:
    if fragment not in source:
        raise ProtocolError(message)


def reject(source: str, fragment: str, message: str) -> None:
    if fragment in source:
        raise ProtocolError(message)


def ordered(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ProtocolError(message)


def validate_protocol(sources: dict[str, str]) -> None:
    lifecycle = sources["lifecycle"]
    vfs = sources["vfs"]
    proc = sources["proc"]

    for field in (
        "uintmembers;",
        "uintactive_operations;",
        "uintdeparting_operations;",
        "intfence_gate;",
        "intclosing;",
    ):
        require(lifecycle, field, f"lifecycle record lost {field}")
    reject(lifecycle, "enumworkflow_lifecycle_state", "public staged lifecycle returned")

    create = function(lifecycle, "workflow_lifecycle_create")
    ordered(
        create,
        (
            "record->members=1",
            "record->active_operations=0",
            "record->departing_operations=0",
            "record->fence_gate=0",
            "record->closing=0",
        ),
        "new workflow is not a clean one-member domain",
    )
    require(create, "workflow_lifecycle_generations[i]==~0ULL", "exhausted generation can wrap")
    require(create, "generation==0", "zero generation can be published")

    join = function(lifecycle, "workflow_lifecycle_join")
    for fragment, message in (
        ("!record->closing", "join can enter a closing workflow"),
        ("!record->fence_gate", "join can cross a workflow fence"),
        ("record->members!=(uint)-1", "member refcount can overflow"),
        ("record->members++", "join does not acquire membership"),
    ):
        require(join, fragment, message)

    leave = function(lifecycle, "workflow_lifecycle_leave")
    ordered(
        leave,
        ("record->members--", "if(record->members==0)", "record->closing=1"),
        "last member does not atomically make the domain finalizer-eligible",
    )

    close = function(lifecycle, "workflow_lifecycle_close")
    for fragment, message in (
        ("record->scope_id!=scope_id", "close is not scope-bound"),
        ("record->controller_control_id!=control_id", "untrusted close is not controller-bound"),
        ("record->fence_gate", "close can race an active fence"),
        ("record->closing=1", "close does not stop new admissions"),
    ):
        require(close, fragment, message)

    operation = function(lifecycle, "workflow_lifecycle_operation_enter")
    for fragment, message in (
        ("!record->closing", "ordinary operation can enter after close"),
        ("record->members>0", "operation can enter an empty generation"),
        ("!record->fence_gate", "operation can cross a fence gate"),
        ("record->active_operations++", "operation is not counted"),
    ):
        require(operation, fragment, message)

    departure = function(lifecycle, "workflow_lifecycle_departure_enter")
    require(departure, "record->members>0", "departure can target an empty generation")
    require(departure, "!record->fence_gate", "departure can cross a fence gate")
    require(departure, "record->departing_operations++", "departure is not counted")
    reject(departure, "!record->closing", "close can deadlock member departure")

    fence_begin = function(lifecycle, "workflow_lifecycle_fence_begin")
    for fragment, message in (
        ("!record->closing", "fence can begin after close"),
        ("record->members>0", "fence can bind an empty generation"),
        ("record->fence_gate=1", "fence does not close operation admission"),
        ("record->active_operations==0", "fence ignores active operations"),
        ("record->departing_operations==0", "fence ignores active teardown"),
        ("if(result<0)record->fence_gate=0", "failed fence leaves the gate closed"),
    ):
        require(fence_begin, fragment, message)

    fence_end = function(lifecycle, "workflow_lifecycle_fence_end")
    for fragment, message in (
        ("record->fence_gate", "fence end accepts an unowned gate"),
        ("record->active_operations==0", "fence end ignores a late operation"),
        ("record->departing_operations==0", "fence end ignores late teardown"),
        ("fence_sequence==record->fence_sequence+1", "fence sequence is not linearized"),
        ("record->fence_gate=0", "fence completion does not reopen admission"),
    ):
        require(fence_end, fragment, message)

    reclaim = function(lifecycle, "workflow_lifecycle_reclaim")
    for fragment, message in (
        ("record->closing", "active lifecycle can be reclaimed"),
        ("record->members==0", "lifecycle can be reclaimed with members"),
        ("record->active_operations==0", "lifecycle can be reclaimed with active operations"),
        ("record->departing_operations==0", "lifecycle can be reclaimed during teardown"),
        ("!record->fence_gate", "lifecycle can be reclaimed under a fence"),
        ("record->used=0", "reclaim does not release its generation slot"),
    ):
        require(reclaim, fragment, message)

    release = function(vfs, "vfs_scope_release")
    ordered(
        release,
        (
            "workflow_lifecycle_departure_leave(lifecycle)",
            "workflow_lifecycle_leave(lifecycle)",
            "vfs_scope_retiring_add_locked(matched)",
            "fs_storage_scope_account_close(storage)",
            "bio_scope_quiesce(scope_id)",
            "agent_background_request()",
        ),
        "last member publishes cleanup before departure/storage/BIO quiescence",
    )

    reject(vfs, "VFS_SCOPE_RECLAIM_", "multi-stage workflow retirement returned")
    prepare = function(vfs, "vfs_scope_reclaim_prepare")
    ordered(
        prepare,
        (
            "workflow_lifecycle_retiring(lifecycle)",
            "agent_scope_reclaim_begin(scope_id,lifecycle,&ignored_target)",
            "ref->cleanup_started=1",
        ),
        "single finalizer does not seal volatile state before file cleanup",
    )
    reap = function(vfs, "vfs_scope_reap_pending")
    reject(reap, "while(", "one background checkpoint can busy-loop retirement")
    ordered(
        reap,
        (
            "vfs_scope_reclaim_prepare(scope_id,&lifecycle,&preserve_files)",
            "bio_background_begin(FS_OWNER_SCOPE(scope_id))",
            "fs_reclaim_scope_files(scope_id)",
            "bio_background_end()",
            "vfs_scope_reclaim_finish(scope_id,lifecycle,preserve_files)",
        ),
        "single finalizer lost bounded file drain ordering",
    )
    require(reap, "if(request_next)agent_background_request()", "unfinished finalization loses its edge")
    finish = function(vfs, "vfs_scope_reclaim_finish")
    ordered(
        finish,
        (
            "bio_scope_retire(scope_id)",
            "resource_account_state_get(ref->storage_account)",
            "workflow_lifecycle_reclaim(lifecycle)",
        ),
        "generation is released before retained storage and BIO settle",
    )

    claim = function(proc, "proc_teardown_claim_locked")
    ordered(
        claim,
        (
            "workflow_lifecycle_departure_enter(lifecycle)",
            "p->teardown_owner_tid=owner",
            "p->teardown_state=PROC_TEARDOWN_QUIESCING",
        ),
        "process teardown is published before its departure token",
    )
    run = function(proc, "proc_teardown_run")
    ordered(
        run,
        (
            "p->files[i]=0",
            "fileclose_batch_add(&close_batch,files[i])",
            "agent_proc_teardown(p)",
            "freepagetable_cleanup(p->pagetable,p->max_page)",
            "vfs_proc_terminal_clear(p)",
            "vfs_proc_lifecycle_release(p)",
        ),
        "process teardown releases identity before files, VM, or Context settle",
    )
    dying = function(proc, "scheduler_finish_dying_thread")
    ordered(
        dying,
        (
            "thread_trapframe_release(t)",
            "proc_thread_resource_release(t)",
            "workflow_lifecycle_departure_leave(lifecycle)",
        ),
        "thread departure completes before trapframe and resource release",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        validate_protocol(load_sources(args.root.resolve()))
    except (ProtocolError, OSError) as error:
        print(f"[teardown-protocol] failed: {error}", file=sys.stderr)
        return 1
    print("[teardown-protocol] member+closing finalizer contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
