#!/usr/bin/env python3
"""Check the bounded system-file allocator wiring without booting a guest."""

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise ContractError(f"missing function: {signature}")
    opening = source.find("{", start + len(signature))
    if opening < 0:
        raise ContractError(f"missing function body: {signature}")
    depth = 0
    state = "code"
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == '"':
                state = "code"
        elif char == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
        index += 1
    raise ContractError(f"unterminated function: {signature}")


def require(text: str, token: str, message: str) -> None:
    if token not in text:
        raise ContractError(message)


def require_order(text: str, first: str, second: str, message: str) -> None:
    first_at = text.find(first)
    second_at = text.find(second)
    if first_at < 0 or second_at < 0 or first_at >= second_at:
        raise ContractError(message)


def check(root: Path) -> None:
    source = (root / "os/file.c").read_text(encoding="utf-8")
    header = (root / "os/file.h").read_text(encoding="utf-8")
    proc = (root / "os/proc.c").read_text(encoding="utf-8")

    require(source, "uint16 free_head;", "freelist head must be a stable index")
    require(source, "uint16 next[FILEPOOLSIZE];", "missing indexed freelist links")
    require(source, "uchar slot_state[FILEPOOLSIZE];", "missing slot state guard")
    require(source, "_Static_assert(FILEPOOLSIZE < 0xffffU", "missing index bound")
    require(source, "volatile uint64 allocation_probes;", "missing allocation probe counter")
    require(source, "volatile uint max_slot_pop_probes;", "missing worst-probe counter")
    require(header, "void filepool_init(void);", "missing allocator init declaration")

    init = function_body(source, "void filepool_init(void)")
    for token, message in (
        ("intr_save()", "allocator init is not serialized"),
        ("filepool_allocator.next[i]", "init does not construct the freelist"),
        ("FILEPOOL_INDEX_NONE", "init does not terminate the freelist"),
        ("filepool_allocator.free_count = FILEPOOLSIZE", "init count is incomplete"),
        ("filepool_allocator.initialized = 1", "init does not publish readiness"),
        ("filepool_assert_locked()", "init omits the debug invariant"),
        ("intr_restore(enabled)", "allocator init does not restore interrupts"),
    ):
        require(init, token, message)

    proc_init = function_body(proc, "void proc_init()")
    require_order(
        proc_init,
        "proc_resource_init();",
        "filepool_init();",
        "filepool must initialize after the resource controller",
    )

    pop = function_body(source, "static uint filepool_pop_locked(void)")
    if re.search(r"\b(for|while)\s*\(", pop):
        raise ContractError("slot allocation must not scan the pool")
    for token, message in (
        ("filepool_allocator.free_head", "pop does not consume the head index"),
        ("FILEPOOL_SLOT_FREE", "pop does not validate slot state"),
        ("filepool_allocator.free_count--", "pop does not update free count"),
        ("filepool_allocator.allocation_probes++", "pop does not count probes"),
        ("filepool_allocator.max_slot_pop_probes = 1", "worst probe is not constant"),
    ):
        require(pop, token, message)

    push = function_body(source, "static void filepool_push_locked(uint index)")
    for token, message in (
        ("FILEPOOL_SLOT_LIVE", "free does not reject duplicate publication"),
        ("filepool[index].ref != 0", "free can publish a referenced slot"),
        ("FILEPOOL_SLOT_FREE", "free does not transition slot state"),
        ("filepool_allocator.next[index] = filepool_allocator.free_head", "free does not link by index"),
        ("filepool_allocator.free_count++", "free does not update free count"),
    ):
        require(push, token, message)

    allocate = function_body(
        source, "int filealloc_many(struct proc *owner, struct file **files, uint count)"
    )
    if re.search(r"\bi\s*<\s*FILEPOOLSIZE", allocate):
        raise ContractError("filealloc_many still scans FILEPOOLSIZE")
    require_order(
        allocate,
        "filepool_allocator.free_count < count",
        "proc_file_slots_reserve(owner, count",
        "capacity must be checked before resource charging",
    )
    require_order(
        allocate,
        "proc_file_slots_reserve(owner, count",
        "filepool_pop_locked()",
        "resource charging must succeed before freelist mutation",
    )
    require(allocate, "files[i] = f;", "allocated slots are not returned in request order")
    require(allocate, "filepool_assert_locked();", "allocation omits the debug invariant")

    close = function_body(
        source,
        "int fileclose_prepare(struct file *f, struct file_close_receipt *receipt)",
    )
    for token, message in (
        ("index = filepool_index_locked(f)", "prepare does not validate a stable pool index"),
        ("FILEPOOL_SLOT_LIVE", "prepare does not validate live ownership"),
        ("f->ref = 0", "prepare does not clear the final reference"),
        ("filepool_push_locked(index)", "final prepare does not return the slot"),
        ("filepool_assert_locked()", "prepare omits the debug invariant"),
    ):
        require(close, token, message)
    require_order(close, "f->ref = 0", "filepool_push_locked(index)",
                  "prepare publishes the slot before clearing its reference")
    push_at = close.find("filepool_push_locked(index)")
    require(close[push_at:], "intr_restore(enabled)",
            "slot return must remain in the interrupt critical section")

    invariant = function_body(source, "static void filepool_assert_locked(void)")
    for token, message in (
        ("#ifdef FILEPOOL_DEBUG", "full invariant is not debug-gated"),
        ("filepool free cycle", "invariant does not detect freelist cycles"),
        ("filepool free count", "invariant does not validate free count"),
        ("filepool membership", "invariant does not validate unique membership"),
        ("filepool live state", "invariant does not validate live slots"),
    ):
        require(invariant, token, message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"check-filepool-freelist: {error}", file=sys.stderr)
        return 1
    print("check-filepool-freelist: indexed O(1) allocator contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
