#!/usr/bin/env python3
"""Check the generation-based open-file authorization fast path."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def function(text: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", text, re.S)
    if not match:
        raise ContractError(f"missing function {name}")
    depth = 1
    pos = match.end()
    while pos < len(text) and depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    if depth:
        raise ContractError(f"unterminated function {name}")
    return compact(text[match.start():pos])


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def check(root: Path) -> None:
    header = compact((root / "os/open_file_io_lease.h").read_text())
    source_text = (root / "os/open_file_io_lease.c").read_text()
    source = compact(source_text)
    file_source = compact((root / "os/file.c").read_text())
    fs_source = compact((root / "os/fs.c").read_text())
    edit_source = compact((root / "os/agent_file_state.c").read_text())

    require("structopen_file_io_token{uint64opaque[4];};" in header,
            "I/O token must stay four words")
    require("uintfile_generations[FILEPOOLSIZE];" in source,
            "file-slot ABA generation is missing")
    require("subject_generations" not in source,
            "duplicated process-generation table returned")
    require("OPEN_FILE_IO_CACHE_CAP64U" in source,
            "bounded grant cache changed")
    require("generationexhausted" not in source and
            source.count("memset(open_file_io_state.grants,0,") >= 2,
            "generation rollover is not fail-closed")

    init = function(source_text, "open_file_io_lease_file_init")
    retire = function(source_text, "open_file_io_lease_file_retire")
    for body in (init, retire):
        require("open_file_io_next32(" in body,
                "file-slot generation is not advanced")
        require("for(" not in body and "while(" not in body,
                "file lifetime hook is no longer O(1)")

    acquire = function(source_text, "open_file_io_lease_acquire")
    validate = function(source_text, "open_file_io_token_validate")
    require(acquire.count("vfs_inode_authorize(") == 1,
            "acquire must have exactly one full VFS authorization")
    require("agent_edit_write_lease_allowed(" in acquire,
            "workflow edit expiry is not captured")
    require(acquire.index("open_file_io_grant_matches_locked(") <
            acquire.index("vfs_inode_authorize("),
            "full authorization precedes the cache fast path")
    require("file_generations[file_slot]" in acquire and
            "edit_generation" in acquire,
            "slow-path publication lacks generation revalidation")
    require("open_file_io_token_seal_locked(token,thread)" in validate and
            "grant->subject==proc" in validate and "grant->file->ip==inode" in validate,
            "filesystem token validation is incomplete")
    require("kernel_receipt_generation" in source and
            "open_file_io_grant_matches_locked(" not in validate,
            "filesystem hand-off repeats the authorization walk")

    require("open_file_io_lease_file_init(f);" in file_source and
            "open_file_io_lease_file_retire(f);" in file_source,
            "filepool lifetime is not wired")
    require("readi_lease(" in file_source and "writei_lease(" in file_source,
            "traditional read/write does not use the lease path")
    require("open_file_io_token_validate(lease,ip,VFS_OP_READ)" in fs_source and
            "open_file_io_token_validate(lease,ip,VFS_OP_WRITE)" in fs_source,
            "filesystem does not verify the kernel token")
    require(edit_source.count("open_file_io_lease_edit_changed();") >= 5,
            "edit authority changes do not invalidate cached grants")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"open-file I/O lease check failed: {error}", file=sys.stderr)
        return 1
    print("open-file I/O lease check: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
