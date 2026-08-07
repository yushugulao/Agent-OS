#!/usr/bin/env python3
"""检查带代际戳的文件系统 epoch 索引。"""

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\(", source)
    if match is None:
        raise ContractError(f"missing function: {name}")
    opening = source.find("{", match.end())
    if opening < 0:
        raise ContractError(f"missing function body: {name}")
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
    raise ContractError(f"unterminated function: {name}")


def require(text: str, token: str, message: str) -> None:
    if token not in text:
        raise ContractError(message)


def require_order(text: str, *tokens: str) -> None:
    cursor = -1
    for token in tokens:
        cursor = text.find(token, cursor + 1)
        if cursor < 0:
            raise ContractError("filesystem epoch index publication order changed")


def check_text(source: str, header: str) -> None:
    for token, message in (
        ("#define FS_EPOCH_INDEX_CAP 64U", "epoch index capacity is not 64"),
        ("uint64 index_generation[FS_EPOCH_INDEX_CAP]", "epoch index is not generation-stamped"),
        ("uchar index_entry_plus_one[FS_EPOCH_INDEX_CAP]", "epoch index lacks compact entry references"),
        ("FS_EPOCH_BUFFER_CAP < FS_EPOCH_INDEX_CAP", "epoch index has no sparse-load invariant"),
    ):
        require(source, token, message)
    require(header, "#define FS_EPOCH_STATS_VERSION 3U", "epoch stats version was not advanced")
    require(header, "uint max_lookup_probes;", "epoch stats omit the worst lookup bound")

    hash_body = function_body(source, "fs_epoch_index_hash")
    for token in ("dev * 0x9e3779b9U", "key ^= key >> 16",
                  "key *= 0x7feb352dU", "key ^= key >> 15"):
        require(hash_body, token, "epoch block hash lost its bounded mixing")

    lookup = function_body(source, "fs_epoch_index_lookup_locked")
    for token, message in (
        ("fs_epoch_index_hash(dev, blockno)", "lookup bypasses the block hash"),
        ("probe <= FS_EPOCH_INDEX_CAP", "lookup is not capacity bounded"),
        ("bucket + probe - 1", "lookup no longer linearly probes collisions"),
        ("epoch.index_generation[slot_index] !=", "lookup accepts a stale epoch slot"),
        ("entry_plus_one > epoch.count", "lookup does not validate its entry reference"),
        ("epoch.totals.max_lookup_probes < probe", "lookup does not measure its worst probe"),
        ("epoch.entries[index].dev == dev", "lookup does not verify the device key"),
        ("epoch.entries[index].blockno == blockno", "lookup does not verify the block key"),
        ('panic("filesystem epoch index saturated")', "index saturation is not fail-closed"),
    ):
        require(lookup, token, message)

    publish = function_body(source, "fs_epoch_index_publish_locked")
    require_order(
        publish,
        "epoch.index_entry_plus_one[slot_index] = entry_index + 1",
        "epoch.index_generation[slot_index] = epoch.active_generation",
    )

    stage = function_body(source, "fs_epoch_stage")
    if re.search(r"for\s*\([^)]*epoch\.count", stage):
        raise ContractError("fs_epoch_stage still scans all staged buffers")
    for token, message in (
        ("fs_epoch_index_lookup_locked(bp->dev, bp->blockno", "stage bypasses the epoch index"),
        ("epoch.totals.deduplicated_stages++", "repeat stage no longer preserves dedup accounting"),
        ("epoch.phase_count[entry->phase]--", "repeat stage no longer moves phase accounting"),
        ("epoch.phase_count[phase]++", "stage no longer accounts its final phase"),
        ("entry->phase == FS_EPOCH_NAMESPACE_DETACH", "repeat stage lost detach/attach exclusion"),
        ("phase == FS_EPOCH_NAMESPACE_ATTACH", "repeat stage lost namespace phase checking"),
        ("fs_epoch_index_publish_locked(publication_slot, entry_index)", "new stage is not published to the index"),
    ):
        require(stage, token, message)
    require_order(
        stage,
        "fs_epoch_bind_owner_locked(owner)",
        "fs_epoch_index_lookup_locked(bp->dev, bp->blockno",
        "epoch.count == FS_EPOCH_BUFFER_CAP",
        "entry_index = epoch.count++",
        "entry->blockno = bp->blockno",
        "fs_epoch_index_publish_locked(publication_slot, entry_index)",
        "bpin(bp)",
    )

    dirty = function_body(source, "fs_epoch_buffer_dirty")
    if re.search(r"\b(for|while)\s*\(", dirty):
        raise ContractError("fs_epoch_buffer_dirty still scans staged buffers")
    require(dirty, "fs_epoch_index_lookup_locked(dev, blockno, 0, 0)",
            "dirty lookup bypasses the epoch index")

    start = function_body(source, "fs_epoch_start_locked")
    require(start, "epoch.active_generation = epoch.next_generation",
            "new epochs do not publish a fresh index generation")
    commit = function_body(source, "fs_epoch_commit")
    require_order(commit, "memset(epoch.entries", "epoch.active_generation = 0")
    init = function_body(source, "fs_epoch_init")
    require(init, "memset(&epoch, 0, sizeof(epoch))",
            "epoch reset does not invalidate the index")


def check(root: Path) -> None:
    check_text(
        (root / "os/fs_epoch.c").read_text(encoding="utf-8"),
        (root / "os/fs_epoch.h").read_text(encoding="utf-8"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"check-fs-epoch-index: {error}", file=sys.stderr)
        return 1
    print("check-fs-epoch-index: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
