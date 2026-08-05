#!/usr/bin/env python3
"""Check lifecycle-indexed Agent file generation tracking."""

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
    match = re.search(rf"{re.escape(name)}\([^;{{}}]*\)\{{", source)
    if match is None:
        raise ContractError(f"missing function: {name}")
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ContractError(f"unterminated function: {name}")


def require(source: str, fragment: str, message: str) -> None:
    if fragment not in source:
        raise ContractError(message)


def reject(source: str, fragment: str, message: str) -> None:
    if fragment in source:
        raise ContractError(message)


def require_order(source: str, fragments: tuple[str, ...], message: str) -> None:
    cursor = -1
    for fragment in fragments:
        cursor = source.find(fragment, cursor + 1)
        if cursor < 0:
            raise ContractError(message)


def check(root: Path) -> None:
    source = compact(root / "os/agent_file_state.c")
    vfs = compact(root / "os/vfs_security.c")

    reject(source, "NPROC", "file generation index still depends on the process table")
    for fragment, message in (
        (
            "#defineAGENT_FILE_CACHE_SCOPE_MAX(VFS_SCOPE_LIFECYCLE_CAP+1U)",
            "file generation index is not lifecycle bounded",
        ),
        (
            "agent_file_cache_scopes[AGENT_FILE_CACHE_SCOPE_MAX]",
            "missing compact generation index",
        ),
        (
            "structworkflow_lifecycle_keylifecycle;",
            "cache entries are not bound to lifecycle generations",
        ),
        (
            "staticuint64agent_file_system_generation;",
            "missing shared SYSTEM visibility epoch",
        ),
    ):
        require(source, fragment, message)

    lookup = function(source, "agent_file_cache_scope_locked")
    reject(lookup, "for(", "file generation lookup scans the cache table")
    reject(lookup, "while(", "file generation lookup is not bounded")
    for fragment, message in (
        (
            "vfs_scope_lifecycle(scope_id,&lifecycle)<0",
            "dynamic cache identity bypasses the authoritative VFS lifecycle",
        ),
        (
            "lifecycle.id>VFS_SCOPE_LIFECYCLE_CAP",
            "lifecycle slot is not range checked",
        ),
        ("slot=lifecycle.id", "dynamic scopes do not use direct lifecycle indexing"),
        (
            "state->scope_id==scope_id&&workflow_lifecycle_key_equal("
            "state->lifecycle,lifecycle)",
            "cache hit omits scope or lifecycle generation validation",
        ),
        (
            "state->cache_generation=scope_id==VFS_SCOPE_SYSTEM?"
            "agent_file_system_generation:agent_file_generation",
            "slot reuse can regress its generation baseline",
        ),
    ):
        require(lookup, fragment, message)

    next_generation = function(
        source, "agent_file_state_generation_next_capture"
    )
    public_next = function(source, "agent_file_state_generation_next")
    require(
        public_next,
        "returnagent_file_state_generation_next_capture(scope_id,0)",
        "public generation publication bypasses the shared lifecycle boundary",
    )
    reject(next_generation, "for(", "tracked writes still broadcast by table scan")
    reject(next_generation, "while(", "tracked writes contain an unbounded walk")
    require_order(
        next_generation,
        (
            "generation=agent_file_counter_next(&agent_file_generation)",
            "state=agent_file_cache_scope_locked(scope_id,1)",
        ),
        "global generation is not allocated before scoped publication",
    )
    for fragment, message in (
        (
            "agent_file_system_generation=generation",
            "SYSTEM updates do not publish one shared visibility epoch",
        ),
        (
            "state->cache_generation=generation",
            "workflow updates do not publish the global monotonic generation",
        ),
    ):
        require(next_generation, fragment, message)
    require(
        next_generation,
        "if(state&&lifecycle)*lifecycle=state->lifecycle",
        "content receipts do not capture lifecycle with generation publication",
    )
    reject(
        next_generation,
        "agent_file_counter_next(&state->cache_generation)",
        "scope-local counters can regress after lifecycle-slot reuse",
    )

    current_generation = function(source, "agent_file_state_scope_generation")
    for fragment, message in (
        (
            "generation=agent_file_generation",
            "unbound lifecycle lookup loses the monotonic fallback",
        ),
        (
            "generation=MAX(state->cache_generation,agent_file_system_generation)",
            "workflow generation does not include SYSTEM visibility",
        ),
    ):
        require(current_generation, fragment, message)

    reclaim = function(source, "agent_file_state_scope_reclaim")
    require_order(
        reclaim,
        (
            "scope_state=agent_file_cache_scope_locked(scope_id,0)",
            "memset(scope_state,0,sizeof(*scope_state))",
            "for(inti=0;i<AGENT_FILE_EDIT_MAX;i++)",
        ),
        "lifecycle reclaim does not retire the generation slot first",
    )

    init = function(source, "agent_file_state_init")
    require(
        init,
        "agent_file_system_generation=0",
        "generation initialization omits the SYSTEM epoch",
    )

    # Cache identity may consume VFS lifecycle truth, but authorization must
    # never consume cache state in the opposite direction.
    reject(
        vfs,
        "agent_file_state_scope_generation",
        "VFS authorization depends on a generation cache",
    )
    reject(vfs, "agent_file_cache", "VFS authorization depends on cache internals")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"agent file generation index check failed: {error}", file=sys.stderr)
        return 1
    print("agent file generation index check passed: direct lifecycle slots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
